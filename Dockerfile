FROM python:3.13-slim-trixie

# 国内构建较慢时，可取消下面两行注释（使用清华 apt 源）
# ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
# RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources

ENV PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY app.py /app/app.py
COPY static /app/static

RUN mkdir -p /config

ENV V2M_CONFIG=/config \
    V2M_ROOTS=/data \
    V2M_DEFAULT_INPUT=/data \
    V2M_PORT=8080

VOLUME ["/config"]
EXPOSE 8080

CMD ["python3", "-u", "/app/app.py"]
