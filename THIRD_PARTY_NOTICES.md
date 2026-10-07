# 第三方组件与素材说明

本项目的 MIT 许可证适用于本项目源码、文档、通用示例与本包原创小猫素材。以下组件和远程服务保留各自的许可证、权利及使用条款：

| 组件或服务 | 用途 | 来源 |
| --- | --- | --- |
| Python / Tkinter | 工作台及脚本运行环境 | https://www.python.org/ |
| Pillow | 表情图片校验与测试图片生成 | https://python-pillow.org/ |
| Node.js | OpenClaw 运行环境 | https://nodejs.org/ |
| OpenClaw | 网关、代理、记忆及语音接口 | https://github.com/openclaw/openclaw |
| @tencent-weixin/openclaw-weixin | 微信渠道接入 | npm 包 @tencent-weixin/openclaw-weixin |
| Fish Audio | 用户自行配置的在线语音合成服务 | https://fish.audio/ |

本包不附带以上组件的二进制安装包或账户凭证。文本与语音模型由使用者自行准备和配置。

启动检查可从官方来源下载缺少的运行组件，安装包保存在使用者本机缓存中，不随仓库分发。Node.js 24.16.0 ZIP 的 SHA-256 来自[官方发布页](https://nodejs.org/en/blog/release/v24.16.0)；完整 Python 3.13.16 Windows ZIP 的 SHA-256 来自[官方 Windows 发布清单](https://www.python.org/ftp/python/3.13.16/windows-3.13.16.json)。这些组件保留各自许可证，安装包中的许可文件随运行目录保留。

开源包不附带音频文件、内置音色或 API Key。语音回复和试听使用用户到 [Fish Audio 官网](https://fish.audio/)获取的 API Key 及所选音色 ID。音色 ID 由用户在工作台配置，单独保存在本地 `profiles/companions/<ID>/fish-service.json`，不随开源包提供。用户须自行持有音色使用许可；工具不自动核实或授予第三方音色许可。

原创人格、模拟对话和原创表情由本项目按 MIT 提供；用户配置的 Fish Audio 音色不包含在该授权范围。见 [素材许可说明](docs/ASSET_LICENSES.md)。

本包的六张小猫表情由新写的几何绘图代码生成，附带 SVG 源码，不使用外部图片或字体。原本地项目中的第三方角色图片和声音试听不包含在本包中。
