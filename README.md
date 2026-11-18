<p align="center">
  <img src="https://raw.githubusercontent.com/kin-w/flv2mp4-web/main/static/logo.svg" width="96" height="96" alt="logo">
</p>

<h1 align="center">视频转 MP4</h1>

<p align="center">带 Web 界面的批量视频转封装工具，把 flv / ts / mkv 等格式无损转成 mp4。</p>

<p align="center">
  <a href="https://hub.docker.com/r/ikimi/flv2mp4-web"><img src="https://img.shields.io/docker/pulls/ikimi/flv2mp4-web" alt="pulls"></a>
  <a href="https://github.com/kin-w/flv2mp4-web/actions/workflows/docker-publish.yml"><img src="https://github.com/kin-w/flv2mp4-web/actions/workflows/docker-publish.yml/badge.svg" alt="build"></a>
</p>

---

## 它解决什么问题

直播录制、监控导出、下载的视频常常是 `.flv` / `.ts` / `.mkv`，很多播放器、相册、NAS 媒体库对它们的支持并不好。把它们**无损转封装**成 `.mp4`（不重新编码、画质不变、速度极快）是最省事的选择。

这个项目在原有一行 ffmpeg 命令的基础上，加了一个可远程访问的 Web 界面：

- 浏览器里选目录、勾文件、点转换，手机也能操作
- 转换完自动删除源文件、自动移动到输出目录，规则可配置
- 实时查看 ffmpeg 原始输出，出问题不用登录服务器看日志

## 功能

| 能力 | 说明 |
|---|---|
| 批量转换 | 目录递归扫描，一次勾选多个文件排队转换 |
| 登录鉴权 | 设置密码后必须登录才能访问，支持「记住登录」30 天，错误尝试次数过多会临时锁定 |
| 无损转封装 | 默认 `-c copy`，不重新编码，速度取决于磁盘 IO |
| 输入目录 / 文件 | 支持指定目录，也支持直接处理单个文件 |
| 输出目录 | 留空则输出到源文件所在目录；设置后统一输出到指定目录 |
| 转换后移动 | 默认开启：先在源目录生成临时文件，成功后再移动到输出目录 |
| 转换后删除源文件 | 默认开启，可在界面里关闭并持久保存 |
| 自定义识别格式 | 默认 `flv,ts,mkv,avi,mov,wmv,f4v,webm,mpg,mpeg,m4v,3gp`，可自行增删 |
| 自定义转换命令 | 命令模板可编辑，支持 `{input}` / `{output}` 占位符，可一键恢复默认 |
| 实时日志 | 每个任务可查看完整 ffmpeg 输出，运行中每 800ms 增量刷新 |
| 断点友好 | 目标 mp4 已存在时自动跳过，绝不覆盖 |
| 移动端适配 | 手机、平板自适应，弹窗为底部抽屉形态 |

## 快速开始

### docker run（默认全盘模式）

把宿主机根目录挂进容器，界面里可以访问任意目录：

```bash
docker run -d --name flv2mp4-web --restart unless-stopped \
  -p 8080:8080 \
  -e TZ=Asia/Shanghai \
  -e V2M_PASSWORD=你的访问密码 \
  -e V2M_ROOTS=/host \
  -e V2M_DEFAULT_INPUT=/host \
  -v /:/host \
  -v ./config:/config \
  ikimi/flv2mp4-web:latest
```

启动后访问 `http://服务器IP:8080`。

### docker compose

仓库根目录的 [docker-compose.yml](docker-compose.yml) 就是这份配置：

```yaml
services:
  flv2mp4:
    image: ikimi/flv2mp4-web:latest
    container_name: flv2mp4-web
    restart: unless-stopped
    ports:
      - "8080:8080"
    environment:
      TZ: Asia/Shanghai
      V2M_PASSWORD: 你的访问密码
      V2M_ROOTS: /host
      V2M_DEFAULT_INPUT: /host
    volumes:
      - /:/host                  # 宿主机根目录
      - ./config:/config         # 设置与会话持久化
```

```bash
docker compose up -d
```

### 路径对应

容器里的 `/` 是镜像自己的文件系统，不能被宿主机覆盖，所以宿主机根目录挂在 `/host`：

| 宿主机 | 界面里填 |
|---|---|
| `/` | `/host` |
| `/home/录播归档` | `/host/home/录播归档` |
| `/mnt/disk2/videos` | `/host/mnt/disk2/videos` |

> 全盘模式下 Web 界面可以读取、删除整台机器的文件，请务必设置 `V2M_PASSWORD`，并用防火墙限制来源 IP，或只在内网使用。

### 只开放部分目录（可选）

不想暴露整台机器时，改成按目录挂载，界面里填容器路径：

```yaml
volumes:
  - /home/bililive-go:/data
environment:
  V2M_ROOTS: /data
  V2M_DEFAULT_INPUT: /data
```

### 多个磁盘 / 多个根目录

界面里显示的、以及你在输入框里填的都是**容器内路径**。要额外挂载别的磁盘，把它们都放进 `V2M_ROOTS`（用 `:` 分隔）：

```yaml
volumes:
  - /home/bililive-go:/data
  - /mnt/disk2/videos:/data2
environment:
  V2M_ROOTS: "/data:/data2"
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `TZ` | `UTC` | 时区，影响日志时间 |
| `V2M_PASSWORD` | 空 | 访问密码；设置后必须登录，留空则无需登录（不推荐公网部署） |
| `V2M_PORT` | `8080` | 监听端口 |
| `V2M_ROOTS` | `/data` | 允许浏览和转换的根目录，多个用 `:` 分隔 |
| `V2M_DEFAULT_INPUT` | `V2M_ROOTS` 第一个 | 首屏默认输入目录 |
| `V2M_CONFIG` | `/config` | 配置文件目录，挂载后设置可持久化 |
| `V2M_FFMPEG` | 自动查找 | ffmpeg 路径，内置镜像已包含 |
| `V2M_FFPROBE` | 自动查找 | ffprobe 路径，用于读取时长显示进度 |
| `V2M_DEBUG` | 关闭 | 设为 `1` 时打印每个 HTTP 请求，排查用 |

## 界面使用

1. **输入目录**：填路径或点「浏览」选择，也可以直接选单个视频文件
2. **输出目录**：留空表示输出到源目录；填写后点「固定」，之后再进来仍然是这个目录
3. **转换后删除源文件**：默认开启
4. **转换后移动到输出目录**：默认开启。关闭后会**直接写入**输出目录，转换中途中断会留下不完整文件，界面会提示风险
5. **识别格式**：输入框里加后缀即可，支持逗号、分号、空格分隔；「默认」按钮恢复
6. **转换命令**：高级设置，可加 `-movflags +faststart` 等参数；必须保留 `{input}` 和 `{output}`
7. **转换任务**：显示进度、速度、耗时，可取消、可查看日志

所有设置实时保存到 `V2M_CONFIG/settings.json`，重启容器不会丢；`恢复默认` 按钮会连同格式列表和命令一起重置。

## 登录与安全

设置 `V2M_PASSWORD` 后，访问任何页面都会先跳到登录页：

- 勾选「记住登录状态」后登录有效期为 **30 天**；不勾选则 **12 小时**
- 会话保存在 `/config/sessions.json`，容器重启后「记住登录」仍然有效
- 同一 IP 连续输错 **5 次**密码会锁定 **10 分钟**（返回 429）
- 密码只存在环境变量里，不在界面中存储或展示；忘记密码改环境变量重启即可
- 图文资源（CSS/JS/logo）和登录接口不校验登录，其余所有接口与页面都需要会话
- 退出按钮在主界面右上角，退出后服务端立即销毁会话（不只是删 cookie）

> 密码通过环境变量明文传入容器，能用 `docker inspect` 看到。对安全要求更高时，建议再套一层反向代理（Nginx + Basic Auth / HTTPS），或只在内网使用。

## 关于「无损」

转换调用 ffmpeg 流复制：

```
ffmpeg -hide_banner -nostdin -y -i {input} -map 0:v:0 -map 0:a? -c copy -f mp4 {output}
```

- 画质与源文件完全一致，速度只受磁盘读写限制
- 能否成功取决于**源编码是否被 mp4 容器支持**：

| 源编码 | 结果 |
|---|---|
| H.264 / H.265 + AAC / MP3 | 正常 |
| TS 里的 H.264 + AAC | 正常 |
| VP8 / VP9 / AV1 | 通常失败，任务里能看到 ffmpeg 报错 |
| AC-3 / DTS 音频 | 可能失败 |

失败时源文件**不会**被删除，临时文件也会被清理。需要处理这类文件时，可以把转换命令改成重新编码，例如：

```
ffmpeg -hide_banner -nostdin -y -i {input} -c:v libx264 -crf 23 -c:a aac -b:a 160k {output}
```

（这会慢很多，并且画质会有轻微损失。）

## 常见问题

**转换中看到 `Invalid NAL unit size` / `missing picture` 警告？**
录制文件本身有损坏帧，ffmpeg 会跳过这些帧继续转，通常最终 mp4 仍可正常播放。点任务右侧「日志」可以看到完整输出。

**为什么任务显示「跳过」？**
目标位置已经存在同名 mp4，程序不会覆盖，避免误删已完成的文件。

**界面显示的是 `/data/xxx`，和宿主机路径不一样？**
界面里一切路径都是容器内视角，宿主机 `/home/bililive-go` 对应容器里的 `/data`。

**提示「输出路径不在允许范围内」？**
容器只能看到被 `-v` 挂载进来的目录。想输出到宿主机 `/home/录播归档`，要么把它挂进容器：

```yaml
volumes:
  - /home/bililive-go:/data
  - /home/录播归档:/archive
environment:
  V2M_ROOTS: "/data:/archive"
```

然后在界面里填容器路径 `/archive`，而不是宿主机路径。
如果确实需要访问宿主机所有目录，可以 `-v /:/host` 并把 `/host` 加入 `V2M_ROOTS`，但这等于把整个文件系统（含删除权限）暴露给 Web 界面，请务必配合密码和防火墙使用。

**转换后文件权限是 root？**
容器默认以 root 运行，产物属主为 root。如需调整，可在 compose 里加 `user: "1000:1000"`（需保证该用户对挂载目录有读写权限）。

**是否安全？**
镜像内没有登录鉴权，任何能访问端口的人都能浏览、转换、删除 `V2M_ROOTS` 下的视频。请只在内网使用，或用防火墙限制来源 IP：

```bash
ufw allow from 你的IP to any port 8080
```

## 技术栈

- 后端：Python 3 标准库（`http.server` + 线程队列），无第三方依赖
- 前端：原生 HTML / CSS / JavaScript，无构建步骤
- 转码：调用系统 ffmpeg，镜像内已内置

## 标签

| 标签 | 说明 |
|---|---|
| `latest` | 主分支最新构建 |
| `sha-xxxxxxx` | 对应提交的构建 |
| `1.0.0` 等 | 打上 `v` 版本标签后自动生成 |

仅提供 `linux/amd64` 镜像。ARM 设备（群晖、树莓派）可自行在 `docker-publish.yml` 中开启多架构构建，或直接本地 `docker build`。
