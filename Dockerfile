FROM eclipse-temurin:8-jdk AS build
WORKDIR /app
COPY src/ src/
RUN mkdir classes && javac -encoding UTF-8 -source 1.8 -target 1.8 -d classes src/*.java

FROM eclipse-temurin:8-jre
WORKDIR /app
COPY --from=build /app/classes/ classes/
EXPOSE 9000
ENTRYPOINT ["java", "-cp", "classes", "S3Server"]
CMD ["/data", "9000", "0.0.0.0"]
