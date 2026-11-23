# everything-mcp

[![CI](https://github.com/Yuiii-owo/everything-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/Yuiii-owo/everything-mcp/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Yuiii-owo/everything-mcp)](https://github.com/Yuiii-owo/everything-mcp/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

一个把 [Everything](https://www.voidtools.com/) 官方 SDK 封装成
[MCP](https://modelcontextprotocol.io/) server 的小工具，让 AI agent 能在 Windows 上
**秒级检索本机文件、文件夹和可执行程序**。

Everything 通过读取 NTFS 主文件表（MFT）建立全盘索引，搜索通常是毫秒级。这个 MCP
server 用 Python 的 `ctypes` 直接调用仓库里自带的 `dll/Everything64.dll`，因此**不需要
编译、不需要额外依赖**，也不需要你在 Everything 里开启 HTTP 服务器。

## 工作原理

```
AI agent  ──stdio(MCP)──>  everything_mcp (FastMCP)  ──ctypes──>  Everything64.dll
                                                                        │ IPC
                                                                        ▼
                                                              Everything.exe (客户端)
```

- SDK DLL 通过 IPC 与正在运行的 Everything 客户端通信，所以 **Everything.exe 必须在运行**。
- SDK 提供 x86、x64、ARM 和 ARM64 四种 DLL。源码运行时从仓库的 `dll/` 加载，
  wheel 安装时从 Python 包内加载。
- SDK 的搜索状态是全局的，且 ctypes 会释放 GIL —— 本 server 用全局锁把每次查询串行化，
  避免并发调用互相污染。

## 前置条件

1. Windows（SDK 仅支持 Windows）。
2. Python 3.10+（一键安装脚本会自动检测；缺失时可通过 winget 安装 Python 3.12）。
3. **Everything 客户端正在运行**（托盘有图标）。下载：<https://www.voidtools.com/downloads/>，
   或 `winget install --id voidtools.Everything -e`。

## 一键安装

在 PowerShell 中执行：

```powershell
irm https://raw.githubusercontent.com/Yuiii-owo/everything-mcp/main/install.ps1 | iex
```

脚本会：

1. 检测 Python 3.10+（缺失时询问是否用 winget 安装 3.12）；
2. 在 `%LOCALAPPDATA%\everything-mcp\venv` 创建独立虚拟环境；
3. 从 GitHub Release 下载 wheel，校验 `SHA256SUMS.txt` 后安装；
4. 运行 `--selftest` 自检；
5. 生成 `%LOCALAPPDATA%\everything-mcp\everything-mcp.cmd` 启动器；
6. 打印可直接粘贴的 MCP 客户端配置。

需要带参数（自动写入客户端配置、指定目录、安装 Everything 客户端等）时：

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/Yuiii-owo/everything-mcp/main/install.ps1))) -Configure both
```

或者下载仓库后双击 `install.bat` / 运行 `install.ps1`：

```powershell
git clone https://github.com/Yuiii-owo/everything-mcp
cd everything-mcp
.\install.bat
# 或
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Configure claude-desktop
```

### 安装脚本参数

| 参数 | 说明 |
|---|---|
| `-InstallDir <路径>` | 安装目录，默认 `%LOCALAPPDATA%\everything-mcp` |
| `-Python <路径>` | 指定 Python 3.10+ 可执行文件 |
| `-Version <vX.Y.Z\|latest>` | 安装指定 Release，默认 `latest` |
| `-Configure none\|claude-desktop\|claude-code\|both` | 自动写入客户端配置，默认 `none` |
| `-InstallEverything` | 检测不到 Everything 客户端时用 winget 安装 |
| `-FromLocal` | 从当前仓库 `dist/*.whl` 或源码安装，而不是下载 Release |
| `-SkipSelftest` | 跳过安装后的自检 |
| `-Yes` | 所有询问默认 yes |

写入 Claude Desktop 配置前会自动备份为 `claude_desktop_config.json.bak`。如果不指定
`-Configure`，脚本只打印配置片段，不修改任何客户端文件。

## 手动安装

从 [Releases](https://github.com/Yuiii-owo/everything-mcp/releases/latest) 下载
`everything_mcp-*.whl` 后：

```powershell
uv venv --python 3.12
uv pip install .\everything_mcp-0.2.1-py3-none-any.whl
```

或直接从源码安装：

```powershell
git clone https://github.com/Yuiii-owo/everything-mcp
cd everything-mcp
uv venv --python 3.12
uv pip install -e ".[dev]"
```

需要 **Python 3.10 或更高**（`mcp` 包的硬性要求，默认的 3.9 跑不了 server）。

## 快速自检（不接 MCP 客户端）

```powershell
# 一键安装后
%LOCALAPPDATA%\everything-mcp\venv\Scripts\python.exe -m everything_mcp --selftest

# 源码环境
.venv\Scripts\python.exe -m everything_mcp --selftest
```

会打印 SDK 状态和一条示例搜索的 JSON。关键字段：

- `dll_path` / `dll_arch` / `host_arch` —— 用的哪个 DLL，是否位数匹配
- `everything_running` —— Everything 客户端是否可达
- `version` —— Everything 版本号
- `sample_search` —— 真实搜索结果

## 接入 AI 客户端

一键安装脚本输出的 JSON 直接可用（`command` 是安装目录里的 console script）：

```json
{
  "mcpServers": {
    "everything": {
      "command": "C:\\Users\\<你>\\AppData\\Local\\everything-mcp\\venv\\Scripts\\everything-mcp.exe",
      "args": []
    }
  }
}
```

- **Claude Desktop**：写入 `%APPDATA%\Claude\claude_desktop_config.json`，或安装时加
  `-Configure claude-desktop`。改完重启客户端。
- **Claude Code**：`-Configure claude-code` 会执行
  `claude mcp add everything -s user -- <安装目录>\venv\Scripts\everything-mcp.exe`；
  也可手动运行。
- **其他客户端**：把上面的 JSON 片段合并进对应配置文件即可。

也可以不写绝对路径，直接使用启动器或已安装的 console script：

```json
{
  "mcpServers": {
    "everything": { "command": "everything-mcp" }
  }
}
```

前提是该命令在客户端的 `PATH` 中。

## 让 agent 主动使用它（可发现性设计）

MCP server 能影响 agent 的地方只有三处：`instructions`（随 initialize 返回，被多数
客户端注入上下文）、工具名、工具描述。这个 server 在这三处都写成了"路由规则"，而不是
只描述功能：

1. **server `instructions`** —— 明确告诉模型：需要找文件/目录/可执行程序时**先用
   `find_files`**，并列出了它应该替代的自带工具（`dir`/`ls`/`find`/`where`/
   `Get-ChildItem`/递归 glob），同时给出正向触发场景（"where is X installed"、
   "find *.psd on D:"、"files modified today"）和不应使用的场景（文件内容搜索、
   已知目录的普通列举），避免模型把"搜索"误当成全文检索。
2. **工具名 `find_files`** —— 不用含义泛化的 `search`（模型容易和 web search、
   代码搜索混淆），名字本身就是"查找磁盘文件"的语义。
3. **工具描述** —— 首句就是触发条件和使用优先级，并点名被替代的 shell/glob 工具；
   每个工具都带 `title` 和 `readOnlyHint`/`idempotentHint` 注解，客户端会据此
   降低确认门槛，也知道它不会改文件。

`tests/test_server.py` 里对这三项都有断言，防止以后修改描述时把路由信息丢掉。

> 如果某个客户端不注入 server `instructions`（只暴露工具列表），工具名和描述仍然
> 生效；再想加强，可以在该客户端的 `AGENTS.md` / 系统提示里加一句
> "locate files with the `find_files` MCP tool instead of ls/glob"。

## 提供的工具

| 工具 | 作用 |
|---|---|
| `find_files` | 核心搜索。支持 Everything 原生语法 + 匹配开关 + 排序、分页 |
| `get_file_info` | 给定完整路径，精确查找索引条目并返回大小、修改时间和属性 |
| `status` | 环境自检：Everything 是否在运行、DLL 路径/架构、版本、是否管理员 |
| `list_options` | 列出所有合法的 `sort` / 匹配开关 / 查询字段，减少 agent 猜参数 |

### `find_files` 参数

- `query`：Everything 搜索语法，例如
  - `ext:py;ts`、`size:>100mb`、`dm:today`、`"C:\project\" ext:py`
  - `folder:node_modules`、`file:*.exe`、`"exact phrase"`、`term1|term2`

  > 注意：Everything 同时索引文件和文件夹，所以 `*.exe` 会连名字以 `.exe` 结尾的
  > **文件夹**一起匹配。只看文件用 `file:*.exe`，只看目录用 `folder:关键词`。
- `max_results` 为 0 到 500，默认 50。传 0 时仅统计匹配总数。
- `offset` 为 0 到 4294967295，默认 0。传入需要跳过的结果条数来翻页。
- `match_case` / `match_path` / `whole_word`
- `regex` 为 `true` 时，将整个查询解析为正则表达式。在查询里用原生 `regex:` 修饰词时保持默认的 `false`。
- `sort`：见 `list_options`，默认 `name_asc`（如 `date_modified_desc`、`size_desc`）
- `paths_only` 默认为 `false`。只需要定位文件时传 `true`，`results` 会返回完整路径字符串数组。

返回结构：

```json
{
  "query": "*.exe",
  "total": 12345,
  "returned": 50,
  "results": [
    {
      "name": "notepad.exe",
      "path": "C:\\Windows\\System32",
      "full_path": "C:\\Windows\\System32\\notepad.exe",
      "extension": "exe",
      "size": 201216,
      "size_human": "196.5 KB",
      "date_modified": "2025-01-01T00:00:00+00:00",
      "is_folder": false,
      "is_volume": false,
      "attributes": ["archive"]
    }
  ]
}
```

## 环境变量

| 变量 | 说明 |
|---|---|
| `EVERYTHING_MCP_DLL` | 覆盖 SDK DLL 路径（默认自动选择随包或源码提供的对应架构 DLL） |
| `EVERYTHING_MCP_EXE` | 指定 `Everything.exe` 路径（自动拉起时用） |
| `EVERYTHING_MCP_AUTOSTART` | 设为 `1` 时，若 Everything 未运行则自动拉起并重试 |

## 常见问题

- **`error_code: 2`（Everything search client is not running）**
  Everything.exe 没开。启动它，或设 `EVERYTHING_MCP_AUTOSTART=1` 让 server 自动拉起。
- **`Could not load the Everything SDK DLL ...`**
  宿主 Python 的位数和所选 DLL 不匹配（例如 32 位 Python 加载了 `Everything64.dll`）。
  装 64 位 Python，或用 `EVERYTHING_MCP_DLL` 指定正确位数的 DLL。
- **搜不到某个盘 / 目录**
  确认该盘已在 Everything 的索引范围内（Everything 选项 → 索引 → 文件夹）。
  另外 Everything 只索引 NTFS（以及可选的其他文件系统）。
- **安装脚本提示无法下载 Release**
  网络无法访问 GitHub API 时，可克隆仓库后运行 `install.ps1 -FromLocal`，
  或从 Releases 页面手动下载 wheel 安装。
- **PowerShell 禁止运行脚本**
  用 `install.bat`，或运行
  `powershell -ExecutionPolicy Bypass -File .\install.ps1`。

## 开发

```powershell
pip install -e ".[dev]"
pytest -q
```

测试里依赖 Everything 客户端的用例在客户端未运行时会自动 skip。

## 目录说明

- `everything_mcp/` —— 本项目源码（Python 包）
- `tests/` —— 测试
- `install.ps1` / `install.bat` —— 一键安装脚本
- `.github/workflows/` —— CI 与 Release 流水线
- `dll/ include/ lib/ src/ ipc/ vs/` —— Everything 官方 SDK 原样保留，未改动

## 许可证

本项目代码以 [MIT](LICENSE) 发布。`dll/ include/ lib/ src/ ipc/ vs/` 下的
Everything SDK 同样为 MIT 许可，版权归 David Carpenter（详见
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)）。Everything 桌面客户端本身是
voidtools 的独立免费软件，不包含在本仓库中。
