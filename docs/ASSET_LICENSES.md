# 示例素材来源与许可

本包示例不使用真人聊天、未经许可的录音、真人声音模型、第三方角色图片或下载的表情图。开源包不附带音频文件或内置音色。

| 素材 | 来源 | 本包许可 |
| --- | --- | --- |
| `examples/persona.md` / `demo_chat.md` | 本项目新编写的虚构人格和模拟对话 | MIT |
| 六张 `assets/stickers/*.png` | 本项目新写的几何绘图代码生成，不引用外部图片，不使用字体 | MIT |
| 六份 `assets/stickers/sources/*.svg` | 同一份几何绘图代码生成的矢量源码 | MIT |

## 在线语音

语音回复和试听使用用户自行配置的 Fish Audio 服务。请到 [Fish Audio 官网](https://fish.audio/)获取自己的 API Key，选择音色并取得音色 ID，再在工作台填写。音色的使用许可、服务额度及可用性以服务方为准；本项目不提供或授予第三方音色许可。

## 表情图片

六张表情包含你好、开心、抱抱、晚安、收到、疑惑。它们由 `scripts/generate_builtin_stickers.py` 从空白画布和原创几何图形生成，附带 SVG 源码。没有读取以前的图片、外部角色素材或字体文件。

重新生成：`python -B -X utf8 scripts/generate_builtin_stickers.py`。

## 使用与再分发

本项目对上表中的原创素材按 [MIT 许可证](../LICENSE)提供使用、修改和再分发许可，请保留许可证及版权声明。`assets/ASSET_PROVENANCE.json` 记录每个示例文件的校验值和生成信息。
