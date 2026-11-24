FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

# 安装 Node.js、Git（影子快照/回滚所需）、ffmpeg（微信语音转码）与 Google 官方 Gemini CLI 运行环境
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    git \
    g++ \
    ffmpeg \
    fonts-wqy-microhei \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @google/gemini-cli@latest \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# 安装无 GPU 纯 CPU 离线语音识别（sherpa-onnx SenseVoice）与微信 SILK 编解码（pilk，独立安装互不阻塞）
RUN pip install --no-cache-dir "sherpa-onnx>=1.10.0" || true
RUN pip install --no-cache-dir "pilk>=0.2.4" || true

COPY . .

EXPOSE 8686

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8686"]
