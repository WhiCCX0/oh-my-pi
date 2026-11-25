# Oh My Pi 简体中文覆盖层

本目录维护本地化资源和构建期转换逻辑。官方业务源码不提交汉化差异；main 通过 GitHub merge-upstream API 非强制同步官方 main。Release 从官方稳定 tag 的精确 commit 构建。

- AST 限定显示位置：TSX 文本/显示属性、CLI 和内置斜杠命令 description、帮助标题。设置在显示投影处翻译，不修改配置键、分组 ID 或枚举值。
- 英文回退；检查命令参数、URL 与命名占位符；不修改系统提示词、用户输入、模型返回、工具协议、JSON/RPC 字段、按键 ID。新增界面或动态模板可能仍为英文，不宣称全量汉化。
- 无新增运行时依赖；使用上游已有 Babel parser。UI 常量在构建期替换，设置只在显示投影时 Map 查找。
- 汉化二进制保留上游版本号，GitHub Release 使用 zh-vX.Y.Z-r1。编译版官方自更新禁用，避免被英文覆盖；升级从本仓库 Release 手动下载。
- 使用官方同版本 npm native leaf（保留上游版本验证）和上游构建脚本；支持 Windows、Linux glibc、macOS 的 x64/arm64，全部六个目标在对应 GitHub 托管架构运行烟测。暂不发布 musl；macOS 采用 ad-hoc 签名，无 Developer ID 公证。
- 发布需通过转换测试、构建、Linux/Windows/macOS 双架构烟测；附 SHA256 校验及上游版本、commit。无 npm 发布或官方发布凭据。

词典基于 yebuwudong/omp-zh 的 dict.json 与 dict-extra.json（MIT，见 LICENSE-dictionary）；overrides.json 用于独立修订。不要添加机器标识的无条件替换。上游改动关键锚点时构建失败，需检查后修订。工作流每 6 小时及手动检查上游；同一发行标签不会覆盖。若需同一上游版本重发，将 workflow 的 ZH_REVISION 增加。

参考 feigo313/oh-my-pi-zh 的资源隔离、yequ172672/oh-my-pi-cn 的显示层边界和 yebuwudong/omp-zh 的 AST 安全原则；未引入其它项目运行时代码。

词典固定版本：https://github.com/yebuwudong/omp-zh/tree/ffdaf6080ade61813b7d581d584d321ea77fa49d

发布时附带原生模块第三方版权声明。发布过程先建草稿、上传全部资产，再公开；同一提交可重试草稿，已公开版本不覆盖。

导出 HTML 的 viewer 内容哈希因构建期转换而变化，因此将校验过的 viewer 内联到汉化二进制生成的 HTML 中，避免指向不存在的官方 CDN 文件；官方共享 Markdown/高亮依赖保持不变。
