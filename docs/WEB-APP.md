# Independent web application / 独立网页应用

LiveCaption supports Windows and macOS. The native Windows overlay remains at `src/python/win_host.py` (`scripts/start.bat`); macOS uses the Swift host. The Windows CPU web adapter is now owned by this repository in `livecaption_web/`.

Personal Agents imports this installed package through the compatibility namespace `modules.caption`; it does not keep a second copy of these files. The package was moved from the hub on 2026-10-24. Native and web presentation adapters remain distinct; consolidating their lower-level backends must preserve their streaming protocols and platform behavior.

To run the web app independently, configure `web-config.toml` using `web-config.example.toml`, reference the existing model directory, and run:

```powershell
D:\GitHub\personalAgents\.venv\Scripts\python.exe -m livecaption_web --config web-config.toml
```

The default independent port is 8872. No running Personal Agents service is required. The `personal-agents` package supplies the shared web/runtime library. A new configuration uses its own data directory; existing hub transcripts remain at their original location. Pointing two services at the same database is refused, so they cannot process the same jobs concurrently.

On a fresh development checkout, keep `personalAgents`, `WeAgent`, `gadget` and the shared `personalAgent/shared` library as sibling sources, then use `uv sync --extra standalone`. This installs references to local packages; it does not copy model weights or business source files.

---

LiveCaption 同时支持 Windows 和 macOS。Windows 独立浮窗继续使用 `scripts/start.bat`；CPU 网页字幕的唯一维护源码已移入本仓库的 `livecaption_web/`。Personal Agents 只保留兼容导入路径，调用安装后的同一份包。

独立网页启动见上面的命令：从 `web-config.example.toml` 建立配置，填写现有模型原件目录，默认打开 `http://127.0.0.1:8872/captions`。无需先启动 Personal Agents 服务，但需要其共享 Python 运行库。新配置不会复制或重置当前中枢已有的字幕数据；同一数据库禁止被两个服务同时持有。

原生浮窗和网页是两种呈现适配器，仍分别保留。底层采集/识别后端尚未全部统一，不能将这次源码归属迁移宣称为所有平台算法已合并。
