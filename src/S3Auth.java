import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import java.io.IOException;
import java.io.Reader;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.time.format.ResolverStyle;
import java.util.*;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/** Header-based AWS Signature V4 verification for the read-only S3 endpoint. */
final class S3Auth {
    private static final String ALGORITHM = "AWS4-HMAC-SHA256";
    private static final String EMPTY_HASH = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    private static final DateTimeFormatter TIMESTAMP = DateTimeFormatter
        .ofPattern("uuuuMMdd'T'HHmmss'Z'").withResolverStyle(ResolverStyle.STRICT);
    private final String accessKey;
    private final String secretKey;

    private S3Auth(String accessKey, String secretKey) {
        this.accessKey = accessKey;
        this.secretKey = secretKey;
    }

    static S3Auth load(Path config) throws IOException {
        Properties properties = new Properties();
        try (Reader reader = Files.newBufferedReader(config, StandardCharsets.UTF_8)) {
            properties.load(reader);
        }
        String access = properties.getProperty("accessKey");
        String secret = properties.getProperty("secretKey");
        if (access == null || !access.matches("[A-Za-z0-9._-]+")
                || secret == null || secret.trim().isEmpty()) {
            throw new IOException("Credential configuration requires a valid accessKey and non-empty secretKey");
        }
        return new S3Auth(access, secret);
    }

    void verify(HttpExchange exchange) throws AuthError {
        Headers headers = exchange.getRequestHeaders();
        String authorization = single(headers, "Authorization");
        if (!authorization.startsWith(ALGORITHM + " ")) throw denied();
        Map<String, String> fields = new HashMap<>();
        for (String field : authorization.substring(ALGORITHM.length() + 1).split(",", -1)) {
            String[] pair = field.trim().split("=", 2);
            if (pair.length != 2 || fields.put(pair[0], pair[1]) != null) throw denied();
        }
        if (fields.size() != 3 || !fields.containsKey("Credential")
                || !fields.containsKey("SignedHeaders") || !fields.containsKey("Signature")) throw denied();
        String[] credential = fields.get("Credential").split("/", -1);
        if (credential.length != 5 || !credential[1].matches("[0-9]{8}")
                || !credential[2].matches("[a-z0-9-]+") || !credential[3].equals("s3")
                || !credential[4].equals("aws4_request")) throw denied();
        if (!MessageDigest.isEqual(bytes(accessKey), bytes(credential[0]))) {
            throw new AuthError("InvalidAccessKeyId", "The access key does not match");
        }
        String signature = fields.get("Signature");
        if (!signature.matches("[0-9a-f]{64}")) throw denied();
        String timestamp = single(headers, "x-amz-date");
        Instant signedAt;
        try {
            signedAt = LocalDateTime.parse(timestamp, TIMESTAMP).toInstant(ZoneOffset.UTC);
        } catch (DateTimeParseException error) { throw denied(); }
        if (!timestamp.substring(0, 8).equals(credential[1])) throw denied();
        if (Math.abs(Instant.now().getEpochSecond() - signedAt.getEpochSecond()) > 900) {
            throw new AuthError("RequestTimeTooSkewed", "Request time must be within 15 minutes of server time");
        }
        String signedHeaders = fields.get("SignedHeaders");
        SortedSet<String> names = new TreeSet<>();
        StringBuilder canonicalHeaders = new StringBuilder();
        for (String name : signedHeaders.split(";", -1)) {
            if (!name.matches("[a-z0-9-]+") || !names.add(name)) throw denied();
            List<String> values = headers.get(name);
            if (values == null || values.isEmpty()) throw denied();
            List<String> normalized = new ArrayList<>();
            for (String value : values) normalized.add(value.trim().replaceAll("[ \\t]+", " "));
            canonicalHeaders.append(name).append(':').append(String.join(",", normalized)).append('\n');
        }
        if (!String.join(";", names).equals(signedHeaders)
                || !names.contains("host") || !names.contains("x-amz-date")) throw denied();
        // All Amazon-specific headers except the payload hash must be signed.
        for (String name : headers.keySet()) {
            String lower = name.toLowerCase(Locale.ROOT);
            if ((lower.startsWith("x-amz-") && !lower.equals("x-amz-content-sha256")
                    || lower.equals("content-md5")) && !names.contains(lower)) throw denied();
        }
        // This server supports permanent credentials only, not session tokens.
        if (headers.containsKey("x-amz-security-token")) throw denied();
        String payloadHash = single(headers, "x-amz-content-sha256");
        if (!payloadHash.equals(EMPTY_HASH) && !payloadHash.equals("UNSIGNED-PAYLOAD")) throw denied();
        String canonicalQuery;
        try { canonicalQuery = canonicalQuery(exchange.getRequestURI().getRawQuery()); }
        catch (IllegalArgumentException error) { throw denied(); }
        String canonical = exchange.getRequestMethod() + "\n" + exchange.getRequestURI().getRawPath()
            + "\n" + canonicalQuery + "\n" + canonicalHeaders + "\n" + signedHeaders + "\n" + payloadHash;
        String scope = String.join("/", Arrays.copyOfRange(credential, 1, 5));
        String stringToSign = ALGORITHM + "\n" + timestamp + "\n" + scope + "\n" + hex(sha256(canonical));
        byte[] key = hmac(bytes("AWS4" + secretKey), credential[1]);
        key = hmac(key, credential[2]);
        key = hmac(key, "s3");
        key = hmac(key, "aws4_request");
        if (!MessageDigest.isEqual(bytes(hex(hmac(key, stringToSign))), bytes(signature))) {
            throw new AuthError("SignatureDoesNotMatch", "The request signature does not match");
        }
    }

    private static String single(Headers headers, String name) throws AuthError {
        List<String> values = headers.get(name);
        if (values == null || values.size() != 1 || values.get(0).isEmpty()) throw denied();
        return values.get(0);
    }

    private static String canonicalQuery(String raw) {
        List<String> pairs = new ArrayList<>();
        if (raw != null && !raw.isEmpty()) for (String part : raw.split("&", -1)) {
            String[] pair = part.split("=", 2);
            pairs.add(encodeQueryPart(pair[0]) + "=" + encodeQueryPart(pair.length == 2 ? pair[1] : ""));
        }
        Collections.sort(pairs);
        return String.join("&", pairs);
    }

    private static String encodeQueryPart(String value) {
        try {
            return URLEncoder.encode(URLDecoder.decode(value.replace("+", "%2B"), "UTF-8"), "UTF-8")
                .replace("+", "%20").replace("*", "%2A").replace("%7E", "~");
        } catch (java.io.UnsupportedEncodingException impossible) { throw new AssertionError(impossible); }
    }

    private static byte[] bytes(String value) { return value.getBytes(StandardCharsets.UTF_8); }

    private static byte[] sha256(String value) {
        try { return MessageDigest.getInstance("SHA-256").digest(bytes(value)); }
        catch (GeneralSecurityException impossible) { throw new AssertionError(impossible); }
    }

    private static byte[] hmac(byte[] key, String value) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(bytes(value));
        } catch (GeneralSecurityException impossible) { throw new AssertionError(impossible); }
    }

    private static String hex(byte[] value) {
        StringBuilder result = new StringBuilder(value.length * 2);
        for (byte b : value) result.append(Character.forDigit((b >> 4) & 15, 16)).append(Character.forDigit(b & 15, 16));
        return result.toString();
    }

    private static AuthError denied() { return new AuthError("AccessDenied", "Valid AWS Signature V4 authentication is required"); }

    static final class AuthError extends Exception {
        final String code;
        AuthError(String code, String message) { super(message); this.code = code; }
    }
}
