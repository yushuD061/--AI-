export type User = { user_id:string; username:string; display_name:string; role:string; store_ids:string[]|null; permissions:string[] }
export type LoginResult = { access_token:string; token_type:string; user:User }
export const authApi = {
  login: (username:string,password:string) => fetch('/api/v1/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username,password})}).then(async r=>{const b=await r.json();if(!r.ok)throw new Error(b.detail||'登录失败');return b.data as LoginResult}),
  me: () => fetch('/api/v1/auth/me').then(async r=>{if(!r.ok)throw new Error('未登录');return (await r.json()).data as User}),
  logout: () => fetch('/api/v1/auth/logout',{method:'POST'}),
}
