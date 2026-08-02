FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y ffmpeg python3.11 python3.11-venv build-essential && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN python3.11 -m venv /opt/venv && /opt/venv/bin/pip install torch==2.7.* torchvision==0.22.* --index-url https://download.pytorch.org/whl/cu128 && /opt/venv/bin/pip install -r requirements.txt
COPY . .
ENV PATH="/opt/venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
