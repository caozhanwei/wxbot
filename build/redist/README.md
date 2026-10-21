# redist/ — VC++ 运行库 DLL（随包 app-local 分发）

`mfc140u.dll` / `msvcp140.dll` 来自 Microsoft VC++ 2015-2022 Redistributable (x64)，
微信 UIA 依赖的 `pywin32 (win32ui)` 需要 MFC 运行库；部分用户机器未装 VC 运行库会导致
启动即崩（ImportError: win32ui / 找不到 MFC140U.DLL），因此按 app-local 方式打进
`_internal`（wxbot.spec 的 binaries），免装运行库。

- 许可：VC Redist 允许应用本地复制分发这些 DLL（main/redist license）。
- 升级：随微信/驱动依赖变化时，从新版 vc_redist.x64.exe 提取同名文件替换即可
  （无 7-Zip 时可从 cab 包 expand -F:* 解出，输出文件带隐藏属性）。

来源版本：14.44.x（VC++ 2015-2022 Redistributable x64，与 %LOCALAPPDATA%\Programs\Wxbot\_internal 中实测可用的副本一致）。
