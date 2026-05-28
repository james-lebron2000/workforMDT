FROM python:3.11-slim

# 国内构建加速 — 切换 apt 源到清华镜像(deb.debian.org 从国内 CVM 下载常常 <50KB/s)
# 如果你在海外构建,把下面 sed 那一行去掉即可(注释掉或删除)
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true

# weasyprint deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 \
    libffi-dev shared-mime-info fonts-noto-cjk \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
# pip 也走清华镜像 — 否则 PyPI 从国内拉同样慢
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
