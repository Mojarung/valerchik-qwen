/** Exercise the actual local UI with its configured live model (no model mocks). */
import assert from "node:assert/strict";
import {mkdir, writeFile} from "node:fs/promises";
import {chromium} from "playwright";

const base = "http://127.0.0.1:8099";
const output = new URL("../../reports/support-live-ui/", import.meta.url);
await mkdir(output, {recursive:true});
const browser = await chromium.launch({headless:true, channel:"chrome"});
const staff = await browser.newContext({viewport:{width:1600,height:1100}});
const visitor = await browser.newContext({viewport:{width:1280,height:1000}});
const operator = await staff.newPage();
const user = await visitor.newPage();
const frames=[];
operator.on("websocket", ws => ws.on("framereceived", frame => {
  try { frames.push(JSON.parse(String(frame.payload))); } catch { /* non-JSON frame */ }
}));
const question="Позовите оператора, как зарегистрироваться на портале?";
try {
  await operator.goto(`${base}/login`);
  await operator.getByLabel("Логин",{exact:true}).fill("support_admin");
  await operator.getByLabel("Пароль",{exact:true}).fill(process.env.ADMIN_PASSWORD);
  await operator.getByRole("button",{name:"Войти",exact:true}).click();
  await operator.waitForURL(/\/admin/);
  await operator.goto(`${base}/operator`);
  await user.goto(base);
  await user.getByRole("textbox",{name:"Сообщение",exact:true}).fill(question);
  await user.getByRole("button",{name:"Отправить",exact:true}).click();
  const cookie=(await visitor.cookies()).find(c=>c.name==="valerchik-chat");
  assert(cookie);
  const [sid,owner]=decodeURIComponent(cookie.value).split(".");
  let state;
  for(let i=0;i<90;i++) {
    state=await (await visitor.request.get(`${base}/api/chat/sessions/${sid}/support`,{headers:{"X-Session-Owner":owner}})).json();
    if(state.status==="waiting_operator") break;
    await user.waitForTimeout(1000);
  }
  assert.equal(state.status,"waiting_operator");
  console.log("PASS live visitor handoff");
  // Identify the exact newly-created session rather than another identical test question.
  const auth=await staff.request.post(`${base}/api/auth/login`,{data:{username:"support_admin",password:process.env.ADMIN_PASSWORD}});
  const token=(await auth.json()).token;
  const take=await staff.request.post(`${base}/api/operator/sessions/${sid}/actions/take`,{headers:{Authorization:`Bearer ${token}`},data:{}});
  assert.equal(take.status(),200);
  await operator.reload();
  const ticket=operator.getByRole("button").filter({hasText:question}).first();
  await ticket.click();
  const box=operator.getByPlaceholder("Ответ пользователю…");
  await box.fill("Мой текст сотрудника");
  const response=operator.waitForResponse(r=>r.url().includes(`/api/operator/sessions/${sid}/draft`)&&r.request().method()==="POST",{timeout:90000});
  const prepare=operator.getByRole("button",{name:"Подготовить",exact:true});
  if(await prepare.count()) await prepare.click();
  else {
    await operator.getByRole("button",{name:"Посмотреть",exact:true}).click();
    await operator.getByRole("button",{name:"Другой вариант",exact:true}).click();
  }
  const draftResponse=await response;
  assert.equal(draftResponse.status(),200);
  const draft=await draftResponse.json();
  assert.equal(draft.generated,true,JSON.stringify({status:draft.status,error:draft.error}));
  assert.equal(draft.status,"ready");
  assert(draft.draft.trim());
  assert(draft.summary.trim());
  assert.equal(await box.inputValue(),"Мой текст сотрудника");
  await operator.getByRole("button",{name:"Вставить в ответ",exact:true}).waitFor();
  const before=frames.length;
  await user.getByRole("textbox",{name:"Сообщение",exact:true}).fill("Я поставщик, регистрирую организацию. Сертификат электронной подписи уже есть. Что проверить дальше?");
  await user.getByRole("button",{name:"Отправить",exact:true}).click();
  let automatic;
  for(let i=0;i<60;i++) {
    automatic=frames.slice(before).find(f=>f.type==="copilot_draft");
    if(automatic) break;
    await operator.waitForTimeout(1000);
  }
  assert(automatic,"Automatic draft was not delivered by WebSocket");
  assert.equal(automatic.generated,true,JSON.stringify({status:automatic.status,error:automatic.error}));
  assert.equal(automatic.status,"ready");
  assert.equal(await box.inputValue(),"Мой текст сотрудника");
  console.log("PASS automatic live copilot after user follow-up; operator text preserved");
  await operator.screenshot({path:new URL("operator.png",output).pathname,fullPage:true});
  await writeFile(new URL("result.json",output),JSON.stringify({passed:true,session_id:sid,question,draft,automatic,websocket_frames:frames.length},null,2));
  console.log(JSON.stringify({passed:true,generated:draft.generated,mode:draft.mode,sources:draft.sources.length,summary:draft.summary,draft:draft.draft,websocket_frames:frames.length}));
} catch(error) {
  await operator.screenshot({path:new URL("failure.png",output).pathname,fullPage:true}).catch(()=>{});
  await writeFile(new URL("failure.json",output),JSON.stringify({error:String(error)},null,2));
  throw error;
} finally {
  await browser.close();
}
