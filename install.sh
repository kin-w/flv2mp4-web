#!/usr/bin/env bash
# ============================================================
# 视频转 MP4 · 一键部署脚本
# 仓库：https://github.com/kin-w/flv2mp4-web
# 用法：bash <(curl -fsSL https://raw.githubusercontent.com/kin-w/flv2mp4-web/main/install.sh) -p 你的密码
# ============================================================
set -euo pipefail

APP_NAME="flv2mp4-web"
IMAGE_USER="ikimi"
IMAGE_NAME="flv2mp4-web"
IMAGE_TAG="latest"
MIRROR=""
PORT="5454"
PASSWORD=""
CONTAINER="flv2mp4-web"
CONF_DIR="/opt/flv2mp4-web/config"
HOST_ROOT="/"
TZ_VALUE="Asia/Shanghai"

C_OK="\033[32m"; C_WARN="\033[33m"; C_ERR="\033[31m"; C_DIM="\033[2m"; C_END="\033[0m"

info()  { printf "${C_OK}[信息]${C_END} %s\n" "$*"; }
warn()  { printf "${C_WARN}[提示]${C_END} %s\n" "$*"; }
error() { printf "${C_ERR}[错误]${C_END} %s\n" "$*" >&2; }
dim()   { printf "${C_DIM}       %s${C_END}\n" "$*"; }

usage() {
  cat <<EOF
视频转 MP4 一键部署脚本

用法：
  bash install.sh [选项]

选项：
  -p <密码>    访问密码（不填则自动生成随机密码）
  -P <端口>    Web 端口，默认 5454
  -d <目录>    挂载的宿主机目录，默认 /（全盘模式）
                例：-d /home/bililive-go 只暴露这个目录
  -c <目录>    配置存放目录，默认 /opt/flv2mp4-web/config
  -n <名称>    容器名称，默认 flv2mp4-web
  -u <用户>    Docker Hub 用户名，默认 ikimi
  -T <标签>    镜像标签，默认 latest
  -m <地址>    镜像加速前缀，例：-m docker.1panel.live
  -t <时区>    时区，默认 Asia/Shanghai
  -h           显示帮助

示例：
  bash install.sh -p mypass
  bash install.sh -p mypass -P 9000 -d /home/bililive-go
  bash <(curl -fsSL https://raw.githubusercontent.com/kin-w/flv2mp4-web/main/install.sh) -p mypass
EOF
}

while getopts ":p:P:d:c:n:u:T:m:t:h" opt; do
  case "$opt" in
    p) PASSWORD="$OPTARG" ;;
    P) PORT="$OPTARG" ;;
    d) HOST_ROOT="$OPTARG" ;;
    c) CONF_DIR="$OPTARG" ;;
    n) CONTAINER="$OPTARG" ;;
    u) IMAGE_USER="$OPTARG" ;;
    T) IMAGE_TAG="$OPTARG" ;;
    m) MIRROR="$OPTARG" ;;
    t) TZ_VALUE="$OPTARG" ;;
    h) usage; exit 0 ;;
    *) error "未知参数：-$OPTARG"; usage; exit 1 ;;
  esac
done

# ---------- 权限 ----------
if [ "$(id -u)" -ne 0 ]; then
  if [ -f "$0" ]; then
    warn "需要 root 权限，尝试使用 sudo 重新执行"
    exec sudo bash "$0" "$@"
  fi
  error "请使用 root 用户运行：curl -fsSL <脚本地址> | sudo bash -s -- -p 你的密码"
  exit 1
fi

# ---------- 系统检查 ----------
if [ ! -f /etc/os-release ]; then
  error "无法识别系统，仅支持 Linux"
  exit 1
fi
. /etc/os-release
info "系统：${PRETTY_NAME:-$NAME}"

# ---------- 安装 Docker ----------
if ! command -v docker >/dev/null 2>&1; then
  warn "未检测到 Docker，开始安装"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://get.docker.com | sh
  else
    error "请先安装 curl 或 wget"
    exit 1
  fi
  systemctl enable --now docker >/dev/null 2>&1 || true
  info "Docker 安装完成"
else
  info "Docker 已安装：$(docker --version | awk '{print $3}' | tr -d ',')"
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  error "未找到 docker compose，请先安装 docker-compose-plugin"
  exit 1
fi

# ---------- 参数校验 ----------
case "$HOST_ROOT" in
  /*) ;;
  *) error "-d 必须是绝对路径"; exit 1 ;;
esac
if [ ! -d "$HOST_ROOT" ]; then
  error "目录不存在：$HOST_ROOT"
  exit 1
fi
if ! printf '%s' "$PORT" | grep -Eq '^[0-9]+$'; then
  error "-P 端口必须是数字"
  exit 1
fi

if [ -z "$PASSWORD" ]; then
  PASSWORD=$(head -c 48 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | cut -c1-12)
  warn "未指定密码，已自动生成"
fi

IMAGE="${IMAGE_USER}/${IMAGE_NAME}:${IMAGE_TAG}"
PULL_IMAGE="${MIRROR:+$MIRROR/}${IMAGE}"

if [ "$HOST_ROOT" = "/" ]; then
  MOUNT_LINE="      - /:/host"
  ROOTS="/host"
  MODE_DESC="全盘模式（宿主机根目录挂载到 /host）"
else
  MOUNT_LINE="      - ${HOST_ROOT}:/data"
  ROOTS="/data"
  MODE_DESC="目录模式（${HOST_ROOT} 挂载到 /data）"
fi

# ---------- 写入 compose ----------
mkdir -p "$CONF_DIR"
COMPOSE_FILE="$(dirname "$CONF_DIR")/docker-compose.yml"

cat > "$COMPOSE_FILE" <<EOF
services:
  ${APP_NAME}:
    image: ${IMAGE}
    container_name: ${CONTAINER}
    restart: unless-stopped
    ports:
      - "${PORT}:5454"
    environment:
      TZ: ${TZ_VALUE}
      V2M_PASSWORD: "${PASSWORD}"
      V2M_ROOTS: ${ROOTS}
      V2M_DEFAULT_INPUT: ${ROOTS}
    volumes:
${MOUNT_LINE}
      - ${CONF_DIR}:/config
EOF

info "配置文件：$COMPOSE_FILE"

# ---------- 拉取镜像 ----------
info "拉取镜像：$PULL_IMAGE"
if [ -n "$MIRROR" ]; then
  docker pull "$PULL_IMAGE"
  docker tag "$PULL_IMAGE" "$IMAGE"
else
  docker pull "$IMAGE"
fi

# ---------- 启动 ----------
if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  warn "容器 $CONTAINER 已存在，重新创建"
  docker rm -f "$CONTAINER" >/dev/null
fi

$COMPOSE -f "$COMPOSE_FILE" up -d

# ---------- 结果 ----------
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "${IP:-}" ] && IP="<服务器IP>"

echo
info "部署完成"
dim "部署模式：$MODE_DESC"
dim "访问地址：http://${IP}:${PORT}"
dim "访问密码：${PASSWORD}"
dim "配置目录：${CONF_DIR}"
dim "容器名称：${CONTAINER}"
dim "查看日志：docker logs -f ${CONTAINER}"
dim "停止服务：docker rm -f ${CONTAINER}"
echo
if [ "$HOST_ROOT" = "/" ]; then
  warn "当前为全盘模式，网页端可读写并删除整台机器的文件，请务必改强密码并限制来源 IP"
fi
