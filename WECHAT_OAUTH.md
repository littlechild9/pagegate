# 微信 OAuth 接入说明

这份文档面向当前 `PageGate` 仓库，说明微信 OAuth 在本项目里的适用边界、推荐接入方式、现有实现状态，以及后续改造建议。

## 一句话结论

- 当前仓库里已经有一版 `PC 网站微信登录` 的实现骨架。
- `手机外部浏览器 -> 唤起微信 App -> 完成网站 OAuth` 不适合作为通用方案。
- 如果要支持手机端，正确分流通常是：
  - `PC 浏览器`：网站应用微信登录
  - `微信内置浏览器`：微信网页授权
  - `外部手机浏览器`：提示“请在微信中打开”或改用其他登录方式

## 适用场景总览

| 场景 | 是否适合微信 OAuth | 官方能力 | 适合本项目吗 | 备注 |
| --- | --- | --- | --- | --- |
| PC 浏览器访问页面 | 是 | 微信开放平台 `网站应用微信登录` | 是 | 当前仓库已接入骨架 |
| 微信内置浏览器访问页面 | 是 | 微信网页授权 | 是，但需要额外接入 | 通常要求服务号 |
| 手机外部浏览器访问页面 | 不适合作为主方案 | 没有稳定的通用 H5 唤起网站登录方案 | 不建议 | 不能把“移动应用微信登录”当成 H5 登录 |
| 原生 iOS / Android App | 是 | `移动应用微信登录` SDK | 不适用当前仓库 | 这是原生 App，不是网页 |

## 当前仓库的现状

项目里已经有以下微信登录骨架：

- 登录页按钮：`templates/login.html`
- 登录入口：`GET /auth/wechat`
- 回调入口：`GET /auth/wechat/callback`
- 配置项：`config.yaml` 里的 `wechat.app_id` / `wechat.app_secret`

当前流程是：

1. 未登录访客访问受控页面。
2. 登录页展示“使用微信登录”按钮。
3. 后端将用户重定向到 `https://open.weixin.qq.com/connect/qrconnect`。
4. 微信回调后，用 `code` 换 `access_token` 和 `openid`。
5. 再请求 `sns/userinfo` 获取昵称和头像。
6. 访客写入 `data/visitors.json`，ID 形如 `wechat_{openid}`。
7. 后续继续复用现有 session、审批制、白名单、私密页逻辑。

这意味着：

- 你不需要重做审批流。
- 你真正需要完成的是“微信身份获取”这一层的正确分流和加固。

## 当前实现存在的问题

当前代码适合作为原型，但还不算生产可用，主要缺口如下：

1. `redirect_uri` 还没有做 URL 编码。
2. `state` 已生成但没有校验，存在 CSRF 风险。
3. 微信 API 返回值只做了最小判断，没有完整检查 `errcode` / `errmsg`。
4. 没有保存 `unionid`。
5. 没有区分 `PC`、`微信内`、`外部手机浏览器` 三类流量。
6. 目前只有一套 `wechat.app_id` / `app_secret` 配置，无法清晰区分“网站应用”和“公众号网页授权”两种微信能力。

## 微信能力边界

### 1. 网站应用微信登录

这是当前仓库已经在使用的方向。

特点：

- 面向网站登录。
- 入口是 `https://open.weixin.qq.com/connect/qrconnect`
- scope 使用 `snsapi_login`
- 官方文档明确将该流程描述为 `PC` 网站场景。
- 微信的“快速登录”说明也明确落在 `Windows / Mac` 已登录微信客户端的场景。

适用于：

- 用户在桌面浏览器里访问你的 PageGate 页面。

不应误解为：

- 适用于任意手机浏览器 H5。
- 适用于“从移动浏览器稳定拉起微信 App 完成网页登录”。

### 2. 移动应用微信登录

这个名字很容易误导。

它指的是：

- 原生 `iOS / Android / HarmonyOS` App
- 需要微信 OpenSDK
- 涉及应用签名、Universal Link、客户端安装检测等原生能力

它不指：

- 普通 H5 页面
- FastAPI 渲染的网页
- 手机浏览器里的网页登录

因此，`PageGate` 当前这种网页项目不能把“移动应用微信登录”当成手机 H5 登录方案。

### 3. 微信网页授权

这是微信内置浏览器里的网页身份获取能力。

特点：

- 运行环境是 `微信内置浏览器`
- 常用于“用户在微信聊天中点开链接”
- 文档写明该能力用于网页授权获取用户身份信息
- 官方文档说明该能力 `仅服务号可用`

这条链路适用于：

- 用户在微信里打开你分享的 PageGate 链接

这条链路不适用于：

- Safari / Chrome 等外部手机浏览器

## 面向 PageGate 的推荐方案

### 推荐分流

`/auth/wechat` 作为统一入口，根据 User-Agent 做分流：

1. `PC 浏览器`
   - 走“网站应用微信登录”
   - 使用 `qrconnect + snsapi_login`

2. `微信内置浏览器`
   - 走“微信网页授权”
   - 使用公众号/服务号网页授权能力

3. `外部手机浏览器`
   - 不直接尝试唤起微信完成网页登录
   - 返回一个提示页：
     - 请在微信中打开此链接
     - 或请改用 PC 扫码登录
     - 或改用钉钉等其他登录方式

### 为什么这样设计

- 这条路径最接近微信官方能力边界。
- 用户体验可预期。
- 不会把“移动应用登录”和“移动网页登录”混在一起。
- 可以最大程度复用当前仓库现有的审批逻辑。

## 配置建议

### 当前仓库的最小配置

当前代码已经支持：

```yaml
wechat:
  app_id: "wx..."
  app_secret: "..."
```

这更像是“网站应用微信登录”的最小配置。

### 推荐的扩展配置

如果后续要同时支持 `PC 网站登录` 和 `微信内网页授权`，建议把配置拆开：

```yaml
wechat:
  website_app_id: "wx..."
  website_app_secret: "..."
  official_account_app_id: "wx..."
  official_account_app_secret: "..."
  enable_inside_wechat: true
```

这样可以避免：

- 同一个 `app_id` 被误认为既能做网站登录又能做网页授权
- 后续维护时分不清哪套密钥属于哪条链路

## 接口设计建议

建议把微信登录拆成 3 个角色明确的入口：

### 1. `GET /auth/wechat`

统一分流入口，负责：

- 读取 `redirect` 目标页
- 检查 User-Agent
- 判断当前请求属于 PC、微信内、还是外部手机浏览器
- 按场景跳转到对应分支

### 2. `GET /auth/wechat/callback`

保留给“网站应用微信登录”使用。

负责：

- 校验 `state`
- 用 `code` 换 `access_token`
- 获取用户信息
- 写入或更新访客记录
- 设置 session cookie
- 重定向回目标页面

### 3. `GET /auth/wechat/oa/callback`

给“微信网页授权”使用。

负责：

- 校验 `state`
- 获取网页授权返回的用户身份
- 写入或更新访客记录
- 设置 session cookie
- 重定向回目标页面

## 安全与稳定性清单

无论采用哪条微信 OAuth 链路，至少应补齐下面这些点：

1. `redirect_uri` 必须 URL 编码。
2. `state` 必须校验，不能只生成不验证。
3. 微信 API 返回值要检查 `errcode` / `errmsg`。
4. 建议保存 `unionid`，为未来跨应用身份归并留空间。
5. 线上使用 HTTPS 时，session cookie 应开启 `secure`。
6. 回调域名必须和微信后台审核配置保持一致。
7. 不要把真实 `app_secret` 提交到仓库。
8. 登录失败时要给用户明确提示，而不是只返回泛化的 502。

## 对当前仓库的实施建议

如果只是先把微信登录补到“可上线试用”，建议分两步：

### 第一步：把现有 PC 登录补完整

- 保留当前 `qrconnect` 方案
- 修复 `redirect_uri` URL 编码
- 加上 `state` 校验
- 加上微信错误处理
- 保存 `unionid`

这一步完成后，你就能稳定支持：

- 桌面浏览器访问 PageGate 页面
- 微信扫码或 PC 微信快速登录

### 第二步：补微信内打开的授权

- 为微信内置浏览器新增专门分支
- 接入网页授权
- 手机外部浏览器显示“请在微信中打开”

这一步完成后，才算真正覆盖“手机里的微信访问场景”。

## 测试清单

每次修改微信 OAuth 相关代码后，至少手动验证以下流程：

1. PC 浏览器访问 `approval` 页面，完成微信登录后进入待审批状态。
2. PC 浏览器访问 `private` 页面，登录后仍然只允许白名单访客查看。
3. 微信内置浏览器访问受控页面，能够进入正确的网页授权分支。
4. 外部手机浏览器访问时，不会误走不可用链路。
5. 登录成功后，`data/visitors.json` 中正确写入 `provider`、`name`、`avatar`。
6. 审批通过后，页面能够正常放行。

## 官方文档

- 网站应用微信登录：
  - https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Wechat_Login.html
- 网站应用 `code -> access_token` / `userinfo`：
  - https://developers.weixin.qq.com/doc/oplatform/Website_App/WeChat_Login/Authorized_Interface_Calling_UnionID.html
- 移动应用微信登录开发指南：
  - https://developers.weixin.qq.com/doc/oplatform/Mobile_App/WeChat_Login/Development_Guide.html
- 微信网页授权：
  - https://developers.weixin.qq.com/doc/offiaccount/OA_Web_Apps/Wechat_webpage_authorization.html

## 针对当前项目的最终判断

对 `PageGate` 来说，比较稳妥的判断是：

- 想支持 `PC`：继续完善当前仓库已有的“网站应用微信登录”。
- 想支持“用户在微信里点开链接”：接入“微信网页授权”。
- 想支持“用户在任意手机浏览器里点开网页，再唤起微信 App 完成网页登录”：不要把这当成主路径。

如果后续要继续实现代码，建议优先完成 `PC 登录补强`，再做 `微信内分流`。
