# AI 陪伴

## 从哪里启动

| 用途 | 入口 | 实际加载 |
| --- | --- | --- |
| Mac 普通聊天 | 双击根目录 `启动.command` | 共用后端、电脑页面；摄像头模块关闭 |
| Mac 摄像头聊天 | 双击 `摄像头/emotion_camera/start.command` | 共用后端、摄像头页面、本机 8765 代理 |
| 手机 | 访问服务器的 `/m.html` | 手机页面，连接同一服务器的后端 |

Mac 首次普通启动会安装 `uv`、Python 3.11、锁定的 Python 依赖，并在缺少时下载语音识别和记忆检索模型。需要联网，模型下载可能较久；已有模型不会重下。摄像头启动器还会准备它自己的代理环境。运行时不需要安装 Node.js，因为仓库包含构建好的 `frontend/`。

`conf.yaml` 中的服务地址、模型和 API 密钥需要按自己的环境设置。密钥不要提交到 Git；手机部署所用的服务器配置也应在服务器上单独管理。

## 目录

| 目录 | 作用 |
| --- | --- |
| `src/` | 电脑和手机共用的 Python 后端；账号规则在 `account_features.py` |
| `frontend-src/src/renderer/src/desktop/` | 电脑页面入口与主界面 |
| `frontend-src/src/renderer/src/mobile/` | 手机页面入口与主界面 |
| `frontend-src/src/renderer/src/` 其他文件 | 两种页面共用的组件、服务和样式 |
| `frontend/` | 已构建的电脑页、手机页和可选摄像头页；部署时直接使用 |
| `摄像头/` | 可选功能的前后端代码、浏览器面板与本机代理 |
| `content/` | 角色配置、头像、背景和表情图片 |
| `live2d-models/` | Live2D 模型 |
| `prompts/prompts.yaml` | 全项目唯一的提示词来源 |
| `models/`、`chat_history/`、`backups/`、`logs/`、`cache/` | 本地模型、个人数据和运行数据，不上传 Git |

## 修改与部署

改前端源码后，在项目根目录运行 `./build_frontend.sh`。它会更新 `frontend/index.html`、`frontend/m.html` 和 `frontend/camera.html`，共用同一份 `frontend/assets/` 与 `frontend/libs/`。普通启动只使用普通电脑页；摄像头启动才使用摄像头页。

Git 仓库应包含源码、`frontend/` 构建产物、锁文件和说明文档。`chat_history/`、`backups/`、本地模型、缓存、私钥和 `.env` 由 `.gitignore` 排除。新机器首次启动会安装依赖和下载所需模型；运行前仍需填写自己的密钥。

同步到阿里云时，先运行 `python3 scripts/sync_to_aliyun.py --dry-run` 查看差异，再运行 `python3 scripts/sync_to_aliyun.py`。脚本使用根目录中未纳入 Git 的 `钥匙1.pem` 通过 SSH 连接，只上传有变化的代码与网页文件；前端源码较新时会先构建。服务器上的聊天记录、长期记忆、账号数据、`conf.yaml`、模型、依赖和服务器独有的角色内容不在删除范围内。同名文件若服务器版本更新，脚本会中止并列出冲突。
