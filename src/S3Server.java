import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.channels.SeekableByteChannel;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.nio.file.attribute.BasicFileAttributes;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.Executors;

/** Small read-only, path-style S3 endpoint. Java 8 standard library only. */
public final class S3Server {
    private final Path root;
    private final S3Auth auth;
    private static final Comparator<String> UTF8_ORDER = (a, b) -> {
        byte[] x = a.getBytes(StandardCharsets.UTF_8), y = b.getBytes(StandardCharsets.UTF_8);
        for (int i = 0; i < Math.min(x.length, y.length); i++) {
            int diff = (x[i] & 255) - (y[i] & 255);
            if (diff != 0) return diff;
        }
        return Integer.compare(x.length, y.length);
    };

    private S3Server(Path root, Path config) throws IOException {
        this.auth = S3Auth.load(config);
        this.root = root.toRealPath();
        if (!Files.isDirectory(this.root)) throw new IOException("Data root must be a directory");
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1 || args.length > 4) {
            System.err.println("Usage: java S3Server DATA_ROOT [PORT=9000] [BIND=127.0.0.1] [CONFIG=s3.properties]");
            System.exit(1);
        }
        S3Server app = new S3Server(Paths.get(args[0]), Paths.get(args.length > 3 ? args[3] : "s3.properties"));
        int port = args.length > 1 ? Integer.parseInt(args[1]) : 9000;
        String bind = args.length > 2 ? args[2] : "127.0.0.1";
        HttpServer server = HttpServer.create(new InetSocketAddress(bind, port), 64);
        java.util.concurrent.ExecutorService workers = Executors.newFixedThreadPool(16);
        server.setExecutor(workers);
        server.createContext("/", app::handle);
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            server.stop(1);
            workers.shutdownNow();
        }));
        server.start();
        System.out.println("Local S3: http://" + bind + ":" + port + " root=" + app.root);
    }

    private void handle(HttpExchange ex) throws IOException {
        Response response = new Response(ex);
        String requestId = UUID.randomUUID().toString();
        ex.getResponseHeaders().set("x-amz-request-id", requestId);
        try {
            try { auth.verify(ex); }
            catch (S3Auth.AuthError error) { throw new ApiError(403, error.code, error.getMessage()); }
            String method = ex.getRequestMethod();
            if (!method.equals("GET") && !method.equals("HEAD")) {
                ex.getResponseHeaders().set("Allow", "GET, HEAD");
                throw new ApiError(405, "MethodNotAllowed", "Only GET and HEAD are supported");
            }
            String path = decode(ex.getRequestURI().getRawPath().replace("+", "%2B"));
            String[] parts = path.substring(1).split("/", 2);
            String bucket = parts[0];
            if (bucket.isEmpty()) throw bad("Use /bucket?list-type=2 or /bucket/key");
            Path dir = bucket(bucket);
            Map<String, String> query = query(ex.getRequestURI().getRawQuery());
            if (parts.length == 1 || parts[1].isEmpty()) {
                if (!method.equals("GET") || !"2".equals(query.get("list-type"))) {
                    throw new ApiError(501, "NotImplemented", "Only ListObjectsV2 is supported for buckets");
                }
                list(response, bucket, dir, query);
            } else {
                for (String parameter : query.keySet()) {
                    if (!parameter.equals("x-id")) {
                        throw new ApiError(501, "NotImplemented", "Object subresources are not supported");
                    }
                }
                get(response, dir, parts[1]);
            }
        } catch (ApiError error) {
            xml(response, error.status, "<Error>" + tag("Code", error.code)
                + tag("Message", error.getMessage()) + tag("RequestId", requestId) + "</Error>");
        } catch (IllegalArgumentException error) {
            xml(response, 400, "<Error>" + tag("Code", "InvalidArgument")
                + tag("Message", "Invalid path or query encoding") + "</Error>");
        } catch (IOException error) {
            if (!response.started) {
                int status = error instanceof AccessDeniedException ? 403
                    : error instanceof NoSuchFileException ? 404 : 500;
                String code = status == 403 ? "AccessDenied" : status == 404 ? "NoSuchKey" : "InternalError";
                xml(response, status, "<Error>" + tag("Code", code)
                    + tag("Message", "Unable to read local data") + tag("RequestId", requestId) + "</Error>");
            }
            System.err.println(requestId + " I/O error: " + error.getMessage());
        } finally {
            ex.close();
        }
    }

    private Path bucket(String name) throws ApiError {
        if (!name.matches("[A-Za-z0-9][A-Za-z0-9._-]*") || name.equals(".") || name.equals("..")) {
            throw new ApiError(403, "AccessDenied", "Invalid bucket path");
        }
        Path dir = root.resolve(name);
        if (Files.isSymbolicLink(dir)) throw denied();
        if (!Files.isDirectory(dir, LinkOption.NOFOLLOW_LINKS)) {
            throw new ApiError(404, "NoSuchBucket", "Bucket does not exist");
        }
        return dir;
    }

    private Path object(Path dir, String key) throws ApiError {
        Path file = dir;
        for (String segment : key.split("/", -1)) {
            if (segment.isEmpty() || segment.equals(".") || segment.equals("..") || segment.indexOf('\\') >= 0) {
                throw denied();
            }
            file = file.resolve(segment);
            if (Files.isSymbolicLink(file)) throw denied();
        }
        if (!Files.isRegularFile(file, LinkOption.NOFOLLOW_LINKS)) {
            throw new ApiError(404, "NoSuchKey", "Object does not exist");
        }
        return file;
    }

    private void get(Response response, Path dir, String key) throws IOException, ApiError {
        HttpExchange ex = response.exchange;
        Path file = object(dir, key);
        BasicFileAttributes attr = Files.readAttributes(file, BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
        long size = attr.size();
        String etag = etag(attr);
        Headers headers = ex.getResponseHeaders();
        headers.set("ETag", etag);
        headers.set("Last-Modified", DateTimeFormatter.RFC_1123_DATE_TIME.format(
            attr.lastModifiedTime().toInstant().atZone(ZoneOffset.UTC)));
        headers.set("Accept-Ranges", "bytes");
        headers.set("Content-Type", "application/octet-stream");
        String match = ex.getRequestHeaders().getFirst("If-Match");
        if (match != null && !match.equals("*") && !Arrays.asList(match.split("\\s*,\\s*")).contains(etag)) {
            throw new ApiError(412, "PreconditionFailed", "Object ETag does not match");
        }
        long start = 0, end = size - 1;
        String range = ex.getRequestHeaders().getFirst("Range");
        if (range != null) {
            try {
                if (!range.matches("bytes=[0-9]*-[0-9]*") || size == 0) throw new NumberFormatException();
                String[] bounds = range.substring(6).split("-", -1);
                if (bounds[0].isEmpty()) {
                    long suffix = Long.parseLong(bounds[1]);
                    if (suffix <= 0) throw new NumberFormatException();
                    start = Math.max(0, size - suffix);
                } else {
                    start = Long.parseLong(bounds[0]);
                    if (!bounds[1].isEmpty()) end = Math.min(end, Long.parseLong(bounds[1]));
                }
                if (start >= size || end < start) throw new NumberFormatException();
            } catch (NumberFormatException error) {
                headers.set("Content-Range", "bytes */" + size);
                throw new ApiError(416, "InvalidRange", "Requested range is not satisfiable");
            }
            headers.set("Content-Range", "bytes " + start + "-" + end + "/" + size);
        }
        long length = size == 0 ? 0 : end - start + 1;
        headers.set("Content-Length", Long.toString(length));
        // Open before headers; never buffer or hash the entire file.
        try (SeekableByteChannel input = Files.newByteChannel(file, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS)) {
            if (ex.getRequestMethod().equals("HEAD") || length == 0) {
                response.sendHeaders(range == null ? 200 : 206, -1);
                return;
            }
            input.position(start);
            response.sendHeaders(range == null ? 200 : 206, length);
            try (OutputStream out = ex.getResponseBody()) {
                byte[] buffer = new byte[64 * 1024];
                long remaining = length;
                while (remaining > 0) {
                    int read = input.read(ByteBuffer.wrap(buffer, 0, (int) Math.min(buffer.length, remaining)));
                    if (read < 0) throw new IOException("File changed during download");
                    out.write(buffer, 0, read);
                    remaining -= read;
                }
            }
        }
    }

    private void list(Response response, String bucket, Path dir, Map<String, String> q) throws IOException, ApiError {
        String prefix = q.getOrDefault("prefix", ""), delimiter = q.getOrDefault("delimiter", "");
        boolean encoded = "url".equals(q.get("encoding-type"));
        if (q.containsKey("encoding-type") && !encoded) throw bad("encoding-type must be url");
        int max;
        try {
            max = Integer.parseInt(q.getOrDefault("max-keys", "1000"));
            if (max < 0 || max > 1000) throw new NumberFormatException();
        } catch (NumberFormatException error) {
            throw bad("max-keys must be between 0 and 1000");
        }
        String context = encode(bucket) + "&" + encode(prefix) + "&" + encode(delimiter);
        String after = q.getOrDefault("start-after", "");
        if (q.containsKey("continuation-token")) {
            try {
                String token = new String(Base64.getUrlDecoder().decode(q.get("continuation-token")), StandardCharsets.UTF_8);
                if (!token.startsWith(context + "\n")) throw new IllegalArgumentException();
                after = token.substring(context.length() + 1);
            } catch (IllegalArgumentException error) {
                throw bad("Invalid continuation-token for this listing");
            }
        }
        SortedMap<String, Path> entries = new TreeMap<>(UTF8_ORDER);
        if (max > 0) Files.walkFileTree(dir, new SimpleFileVisitor<Path>() {
            @Override public FileVisitResult visitFile(Path file, BasicFileAttributes attr) {
                if (!attr.isRegularFile()) return FileVisitResult.CONTINUE;
                String key = dir.relativize(file).toString().replace(java.io.File.separatorChar, '/');
                if (key.indexOf('\\') >= 0 || !key.startsWith(prefix)) return FileVisitResult.CONTINUE;
                int split = delimiter.isEmpty() ? -1 : key.indexOf(delimiter, prefix.length());
                if (split >= 0) entries.put(key.substring(0, split + delimiter.length()), null);
                else entries.put(key, file);
                return FileVisitResult.CONTINUE;
            }
        });
        List<Map.Entry<String, Path>> page = new ArrayList<>();
        boolean truncated = false;
        for (Map.Entry<String, Path> entry : entries.entrySet()) {
            if (UTF8_ORDER.compare(entry.getKey(), after) <= 0) continue;
            if (page.size() == max) { truncated = true; break; }
            page.add(entry);
        }
        StringBuilder xml = new StringBuilder("<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\">");
        xml.append(tag("Name", bucket)).append(tag("Prefix", value(prefix, encoded)))
            .append(tag("KeyCount", Integer.toString(page.size())))
            .append(tag("MaxKeys", Integer.toString(max)))
            .append(tag("IsTruncated", Boolean.toString(truncated)));
        if (encoded) xml.append(tag("EncodingType", "url"));
        if (q.containsKey("delimiter")) xml.append(tag("Delimiter", value(delimiter, encoded)));
        if (q.containsKey("start-after")) xml.append(tag("StartAfter", value(q.get("start-after"), encoded)));
        if (q.containsKey("continuation-token")) xml.append(tag("ContinuationToken", q.get("continuation-token")));
        if (truncated) {
            String token = Base64.getUrlEncoder().withoutPadding().encodeToString(
                (context + "\n" + page.get(page.size() - 1).getKey()).getBytes(StandardCharsets.UTF_8));
            xml.append(tag("NextContinuationToken", token));
        }
        for (Map.Entry<String, Path> entry : page) {
            String key = value(entry.getKey(), encoded);
            if (entry.getValue() == null) {
                xml.append("<CommonPrefixes>").append(tag("Prefix", key)).append("</CommonPrefixes>");
            } else {
                BasicFileAttributes attr = Files.readAttributes(entry.getValue(), BasicFileAttributes.class, LinkOption.NOFOLLOW_LINKS);
                xml.append("<Contents>").append(tag("Key", key))
                    .append(tag("LastModified", attr.lastModifiedTime().toInstant().toString()))
                    .append(tag("ETag", etag(attr))).append(tag("Size", Long.toString(attr.size())))
                    .append(tag("StorageClass", "STANDARD")).append("</Contents>");
            }
        }
        xml(response, 200, xml.append("</ListBucketResult>").toString());
    }

    private static String etag(BasicFileAttributes attr) {
        // Opaque metadata validator, not an MD5 content digest.
        return "\"" + Long.toHexString(attr.size()) + "-" + Long.toHexString(attr.lastModifiedTime().toMillis()) + "\"";
    }

    private static Map<String, String> query(String raw) throws ApiError {
        Map<String, String> result = new HashMap<>();
        if (raw != null && !raw.isEmpty()) for (String part : raw.split("&")) {
            String[] pair = part.split("=", 2);
            String key = decode(pair[0].replace("+", "%2B"));
            if (result.put(key, pair.length > 1 ? decode(pair[1].replace("+", "%2B")) : "") != null) throw bad("Duplicate query parameter");
        }
        return result;
    }

    private static String decode(String s) {
        try { return URLDecoder.decode(s, "UTF-8"); }
        catch (java.io.UnsupportedEncodingException impossible) { throw new AssertionError(impossible); }
    }

    private static String encode(String s) {
        try { return URLEncoder.encode(s, "UTF-8").replace("+", "%20").replace("*", "%2A").replace("%7E", "~"); }
        catch (java.io.UnsupportedEncodingException impossible) { throw new AssertionError(impossible); }
    }

    private static String value(String s, boolean encoded) { return encoded ? encode(s) : s; }

    private static String tag(String name, String value) {
        return "<" + name + ">" + value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&apos;") + "</" + name + ">";
    }

    private static void xml(Response response, int status, String body) throws IOException {
        HttpExchange ex = response.exchange;
        byte[] bytes = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>" + body).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/xml; charset=utf-8");
        ex.getResponseHeaders().remove("Content-Length");
        if (ex.getRequestMethod().equals("HEAD")) response.sendHeaders(status, -1);
        else {
            response.sendHeaders(status, bytes.length);
            ex.getResponseBody().write(bytes);
        }
    }

    private static final class Response {
        final HttpExchange exchange;
        boolean started;
        Response(HttpExchange exchange) { this.exchange = exchange; }
        void sendHeaders(int status, long length) throws IOException {
            started = true;
            exchange.sendResponseHeaders(status, length);
        }
    }

    private static ApiError bad(String message) { return new ApiError(400, "InvalidArgument", message); }
    private static ApiError denied() { return new ApiError(403, "AccessDenied", "Path is not permitted"); }

    private static final class ApiError extends Exception {
        final int status;
        final String code;
        ApiError(int status, String code, String message) {
            super(message);
            this.status = status;
            this.code = code;
        }
    }
}
