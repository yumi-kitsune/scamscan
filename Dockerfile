FROM python:3-alpine

WORKDIR /app
COPY scan.py pyproject.toml ./
RUN ["pip", "install", "."]

ENTRYPOINT ["scamscan"]
