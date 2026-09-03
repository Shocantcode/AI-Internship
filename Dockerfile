FROM apache/airflow:3.3.0

USER root

RUN apt-get -o Acquire::Max-FutureTime=3600 update \
    && apt-get install -y --no-install-recommends \
        openjdk-17-jdk-headless \
        procps \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libx11-6 \
        libxcb1 \
        libxcomposite1 \
        libxdamage1 \
        libxext6 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        libxshmfence1 \
        libxss1 \
        libxtst6 \
        xdg-utils \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/spark-extra-jars \
    && python -c "import urllib.request; urls = {'hadoop-aws-3.3.4.jar': 'https://repo.maven.apache.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar', 'aws-java-sdk-bundle-1.12.262.jar': 'https://repo.maven.apache.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.262/aws-java-sdk-bundle-1.12.262.jar', 'wildfly-openssl-1.0.7.Final.jar': 'https://repo.maven.apache.org/maven2/org/wildfly/openssl/wildfly-openssl/1.0.7.Final/wildfly-openssl-1.0.7.Final.jar'}; [urllib.request.urlretrieve(url, '/opt/spark-extra-jars/' + name) for name, url in urls.items()]" \
    && chown -R airflow:root /opt/spark-extra-jars

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=$JAVA_HOME/bin:$PATH

USER airflow

COPY requirements.txt /tmp/requirements.txt

RUN pip install --no-cache-dir -r /tmp/requirements.txt

USER root

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN mkdir -p /ms-playwright \
    && PYTHONPATH=/home/airflow/.local/lib/python3.11/site-packages \
         python -m playwright install chromium \
    && chmod -R 755 /ms-playwright

USER airflow