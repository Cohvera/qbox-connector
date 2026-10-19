FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 libiodbc2 ca-certificates curl unzip libstdc++6 libxml2 libxslt1.1 libcurl4 libfontconfig1 libfreetype6 && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --retry 3 --connect-timeout 20 --max-time 300 \
      https://package.windev.com/pack/wx31/wx316017/commun/wxpack_iodbclinux316017.zip -o /tmp/driver.zip \
 && echo '127eb0c57882d474d4b8e964fd0cbb89edf44e3c20f2f5b96f74f1fad9e1e3ef  /tmp/driver.zip' | sha256sum -c - \
 && mkdir /driver && unzip -q /tmp/driver.zip '*.so' -d /driver && rm /tmp/driver.zip
ENV LD_LIBRARY_PATH=/driver PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
COPY reader.py /app/reader.py
# The reader runs as the configuration owner, not root. Do not inherit a restrictive host umask.
RUN chmod 755 /app && chmod 644 /app/reader.py
ENTRYPOINT ["python3", "/app/reader.py"]
