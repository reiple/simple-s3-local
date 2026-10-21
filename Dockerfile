FROM eclipse-temurin:8-jdk AS build
WORKDIR /app
COPY src/ src/
COPY build.sh ./
RUN sh build.sh

FROM eclipse-temurin:8-jre
WORKDIR /app
COPY --from=build /app/build/local-s3.jar ./local-s3.jar
EXPOSE 9000
ENTRYPOINT ["java", "-jar", "local-s3.jar"]
CMD ["/data", "9000", "0.0.0.0"]
