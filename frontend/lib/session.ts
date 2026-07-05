/**
 * client session token（Phase 14 关注点 6，2026-07-05 用户指定）：
 * 浏览器侧生成一次 UUID 存 localStorage，随每个 API 请求经 X-Client-Session
 * 头发送；后端简单信任、仅做日志关联（暂无账户系统，无鉴权语义）。
 */

const KEY = "pdfc_client_session";

export function getClientSession(): string {
  if (typeof window === "undefined") return "ssr";
  let token = window.localStorage.getItem(KEY);
  if (!token) {
    token = crypto.randomUUID();
    window.localStorage.setItem(KEY, token);
  }
  return token;
}
