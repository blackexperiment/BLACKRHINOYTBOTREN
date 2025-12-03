# Dockerfile
FROM python:3.10-slim-bullseye

ENV PYTHONUNBUFFERED=1
WORKDIR /app

# Install system deps (ffmpeg required for video processing by yt-dlp)
RUN apt-get update -y && \
    apt-get install -y --no-install-recommends \
      gcc \
      libffi-dev \
      libssl-dev \
      ffmpeg \
      aria2 \
      git \
    && rm -rf /var/lib/apt/lists/*

# Copy files
COPY . /app

# Upgrade pip & install python deps
RUN pip install --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Expose web port for Render
ENV PORT=10000
EXPOSE 10000

# Run the bot (which also kicks off web server)
CMD ["python3", "bot.py"]
