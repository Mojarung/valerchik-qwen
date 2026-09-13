/** Support acceptance: isolated users, browser UI, durable APIs and HTTP races. */
import assert from "node:assert/strict";
import {mkdir, writeFile} from "node:fs/promises";
import {chromium} from "playwright";

const BASE = process.env.SUPPORT_E2E_BASE || "http://127.0.0.1:8099";
const output = new URL("../../reports/support-e2e/", import.meta.url);
await mkdir(output, {recursive: true});
const password = process.env.OPERATOR_PASSWORD;
const adminPassword = process.env.ADMIN_PASSWORD;
const expectedLine = Number(process.env.EXPECTED_LINE || 1);
const expectedDirection = process.env.EXPECTED_DIRECTION || "Консультации";
const transferLine = Number(process.env.TRANSFER_LINE || 2);
assert(password && adminPassword, "Test credentials must be supplied by environment");
const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || "chrome"});
const checks = [];
const contexts = [];
const startedAt = Date.now();
let checkpointAt = startedAt;
const mark = name => {
  const duration_ms = Date.now() - checkpointAt;
  checkpointAt = Date.now();
  checks.push({name, duration_ms});
  console.log(`PASS ${name} (${duration_ms} ms)`);
};
async function account(name, secret = password) {
  const context = await browser.newContext({viewport: {width: 1440, height: 1050}});
  contexts.push(context);
  const page = await context.newPage();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Логин", {exact:true}).fill(name);
  await page.getByLabel("Пароль", {exact:true}).fill(secret);
  await page.getByRole("button", {name:"Войти", exact:true}).click();
  await page.waitForURL(/\/(operator|admin)$/);
  const auth = await context.request.post(`${BASE}/api/auth/login`, {data:{username:name,password:secret}});
  const token = (await auth.json()).token;
  await page.goto(`${BASE}/operator`);
  return {context, page, token};
}
async function post(target, path, body = {}, headers = {}) {
  const context = target.context ?? target;
  const authorization = target.token ? {Authorization:`Bearer ${target.token}`} : {};
  return context.request.post(BASE + path, {data:body, headers:{"X-Requested-With":"valerchik", ...authorization, ...headers}});
}
const stateOf = async (context, sid, owner) => (await context.request.get(`${BASE}/api/chat/sessions/${sid}/support`,
  {headers:{"X-Session-Owner":owner}})).json();
async function poll(fn, accept, milliseconds=15000) {
  const end=Date.now()+milliseconds;
  while(Date.now()<end) { const value=await fn(); if(accept(value)) return value; await new Promise(r=>setTimeout(r,200)); }
  throw Error("Timed out waiting for expected state");
}
try {
  const [one,two,tech,admin] = await Promise.all([
    account(process.env.OPERATOR_ONE_USER || "support_one"),
    account(process.env.OPERATOR_TWO_USER || "support_two"),
    account(process.env.TECH_USER || "support_tech"),
    account(process.env.ADMIN_USER || "support_admin",adminPassword),
  ]);
  assert([one,two,tech,admin].every(account => account.token));
  mark("Four independent staff browser sessions authenticate through UI and API");
  const visitor = await browser.newContext({viewport:{width:1280,height:1000}});
  contexts.push(visitor);
  const created = await (await post(visitor,"/api/chat/sessions",{audience:"supplier"})).json();
  const {session_id:sid,owner}=created;
  assert(sid && owner);
  mark("Visitor creates an owned supplier conversation");
  const headers={"X-Session-Owner":owner};
  await visitor.addCookies([{name:"valerchik-chat",value:`${sid}.${owner}`,url:BASE}]);
  const user=await visitor.newPage();
  await user.goto(BASE);
  const questionText=process.env.SUPPORT_E2E_QUESTION ||
    `Позовите оператора, как зарегистрироваться на портале? Проверка support-flow ${Date.now()}`;
  const question=`${questionText} Ответ не помог`;
  await user.getByRole("textbox",{name:"Сообщение",exact:true}).fill(question);
  await user.getByRole("button",{name:"Отправить",exact:true}).click();
  await user.getByText(question,{exact:true}).waitFor();
  mark("Visitor sends the handoff request through the chat UI");
  const queued=await poll(()=>stateOf(visitor,sid,owner),s=>s.status==="waiting_operator");
  assert.equal(queued.line,expectedLine);
  mark("Contextual routing sends the ticket to its expected direction");
  let history=await (await visitor.request.get(`${BASE}/api/chat/sessions/${sid}/messages`,{headers})).json();
  const transferMessage=history.find(message=>message.role==="assistant"&&message.content.includes("специалист"));
  assert(transferMessage && !/линии?\s+[123]/i.test(transferMessage.content));
  mark("New visitor notification names the direction without numeric lines");
  const takes=await Promise.all([one,two].map(a=>post(a,`/api/operator/sessions/${sid}/actions/take`)));
  assert.deepEqual(takes.map(r=>r.status()).sort(),[200,409]);
  mark("Concurrent take allows exactly one operator from the direction");
  const winner=takes[0].status()===200?one:two;
  const loser=winner===one?two:one;
  const assigned=await stateOf(visitor,sid,owner);
  assert.equal(assigned.status,"operator_active");
  assert(assigned.assigned_operator_id);
  mark("Assignment and acceptance become durable visitor state");
  assert.equal((await post(loser,`/api/operator/sessions/${sid}/message`,{text:"Чужой ответ"})).status(),403);
  mark("Second same-line operator cannot answer another owner's ticket");
  assert.equal((await tech.context.request.get(`${BASE}/api/operator/sessions/${sid}/messages`,{headers:{Authorization:`Bearer ${tech.token}`}})).status(),403);
  mark("Operator from another direction cannot read the conversation");
  await winner.page.reload();
  await winner.page.getByRole("button").filter({hasText:question}).click();
  await winner.page.getByText(expectedDirection,{exact:true}).first().waitFor();
  assert.equal(await winner.page.getByText(/Линия\s+[123]/i).count(),0);
  assert.equal(await winner.page.getByRole("complementary",{name:"База знаний"}).count(),0);
  mark("Operator sees a compact direction header with knowledge base closed by default");
  const panel=winner.page.getByRole("region",{name:"Сведения об обращении"});
  await panel.getByText(questionText,{exact:true}).waitFor();
  await panel.getByText("Ответ бота не помог",{exact:true}).waitFor();
  assert.equal(await panel.getByText("Роль: Не уточнена",{exact:true}).count(),0);
  assert.equal(await panel.getByText("Тема:",{exact:true}).count(),0);
  mark("Ticket card separates the question from bot-failure cause and hides empty fields");
  const reply="Проверьте, пожалуйста, адрес страницы регистрации.";
  const replyBox=winner.page.getByPlaceholder("Ответ пользователю…");
  await replyBox.fill(reply);
  await replyBox.press("Enter");
  await user.getByText(reply,{exact:true}).waitFor();
  mark("Operator UI reply reaches visitor over durable delivery");
  const draftText="Мой незавершённый ответ";
  await replyBox.fill(draftText);
  const prepare=winner.page.getByRole("button",{name:"Подготовить",exact:true});
  if(await prepare.count()) await prepare.click();
  else {
    await winner.page.getByRole("button",{name:"Посмотреть",exact:true}).click();
    await winner.page.getByRole("button",{name:"Другой вариант",exact:true}).click();
  }
  await winner.page.waitForTimeout(1500);
  assert.equal(await replyBox.inputValue(),draftText);
  mark("Copilot generation never overwrites typed operator text");
  await winner.page.reload();
  await winner.page.getByRole("button").filter({hasText:question}).click();
  const reloadedPanel=winner.page.getByRole("region",{name:"Сведения об обращении"});
  await reloadedPanel.getByRole("button",{name:"Уточнить",exact:true}).click();
  await reloadedPanel.getByLabel("Текст действия").fill("Какой браузер вы используете?");
  await reloadedPanel.getByRole("button",{name:"Отправить вопрос",exact:true}).click();
  await poll(()=>stateOf(visitor,sid,owner),s=>s.status==="waiting_user"&&s.waiting_reason==="information");
  mark("Operator requests missing information through the compact action panel");
  assert.equal((await post(visitor,`/api/chat/sessions/${sid}/solution`,{solved:true},headers)).status(),409);
  mark("Information request cannot be falsely confirmed as a solution");
  await user.getByRole("textbox",{name:"Сообщение",exact:true}).fill("Использую Firefox");
  await user.getByRole("button",{name:"Отправить",exact:true}).click();
  await poll(()=>stateOf(visitor,sid,owner),s=>s.status==="operator_active");
  mark("Visitor reply returns a waiting ticket to active work");
  const repeat={text:"Откройте настройки сертификата",request_id:"browser-retry-1"};
  assert.equal((await post(winner,`/api/operator/sessions/${sid}/message`,repeat)).status(),200);
  assert.equal((await post(winner,`/api/operator/sessions/${sid}/message`,repeat)).status(),200);
  history=await (await visitor.request.get(`${BASE}/api/chat/sessions/${sid}/messages`,{headers})).json();
  assert.equal(history.filter(m=>m.content===repeat.text).length,1);
  mark("Repeated message ID does not duplicate the message");
  await visitor.setOffline(true);
  const missed="Сообщение во время отключения пользователя";
  assert.equal((await post(winner,`/api/operator/sessions/${sid}/message`,{text:missed})).status(),200);
  await visitor.setOffline(false);
  await user.getByText(missed,{exact:true}).waitFor({timeout:25000});
  mark("Visitor reconnect fetches missed messages");
  assert.equal((await post(winner,`/api/operator/sessions/${sid}/actions/transfer`,{line:transferLine})).status(),200);
  const transferred=await stateOf(visitor,sid,owner);
  assert.equal(transferred.line,transferLine);
  assert.equal(transferred.status,"waiting_operator");
  mark("Transfer without a required comment changes direction while keeping the same conversation");
  assert.equal((await post(winner,`/api/operator/sessions/${sid}/message`,{text:"После перевода"})).status(),403);
  mark("Former owner immediately loses write access after transfer");
  assert.equal((await post(tech,`/api/operator/sessions/${sid}/actions/take`)).status(),200);
  const transferredHistory=await (await tech.context.request.get(`${BASE}/api/operator/sessions/${sid}/messages`,{headers:{Authorization:`Bearer ${tech.token}`}})).json();
  assert(transferredHistory.some(message=>message.content===question));
  mark("New-direction operator accepts the ticket with its full history");
  assert.equal((await post(tech,`/api/operator/sessions/${sid}/actions/resolve`)).status(),200);
  const reopenResponse=user.waitForResponse(r=>r.url().includes(`/api/chat/sessions/${sid}/solution`)&&r.request().method()==="POST");
  await user.getByRole("button",{name:"Не решено",exact:true}).click();
  assert.equal((await reopenResponse).status(),200);
  const reopened=await poll(()=>stateOf(visitor,sid,owner),s=>["operator_active","waiting_operator"].includes(s.status));
  if(reopened.status==="waiting_operator") assert.equal((await post(tech,`/api/operator/sessions/${sid}/actions/take`)).status(),200);
  mark("Not solved returns the ticket to the previous available operator or queue");
  assert.equal((await post(tech,`/api/operator/sessions/${sid}/actions/resolve`)).status(),200);
  const solvedResponse=user.waitForResponse(r=>r.url().includes(`/api/chat/sessions/${sid}/solution`)&&r.request().method()==="POST");
  await user.getByRole("button",{name:"Помогло",exact:true}).click();
  assert.equal((await solvedResponse).status(),200);
  await poll(()=>stateOf(visitor,sid,owner),s=>s.status==="resolved");
  mark("Helped is the only visitor action that resolves the ticket");
  await user.reload();
  await user.getByText(question,{exact:true}).waitFor();
  history=await (await visitor.request.get(`${BASE}/api/chat/sessions/${sid}/messages`,{headers})).json();
  assert(history.some(m=>m.content===question)&&history.some(m=>m.content===missed));
  assert.equal(history.filter(m=>m.meta?.kind==="feedback_request").length,1);
  mark("Reload restores the complete conversation history");
  await user.screenshot({path:new URL("visitor.png",output).pathname,fullPage:true});
  await tech.page.screenshot({path:new URL("operator.png",output).pathname,fullPage:true});
  mark("Resolved conversation contains exactly one rating invitation");
  const durations=checks.map(check=>check.duration_ms).sort((a,b)=>a-b);
  const percentile=value=>durations[Math.min(durations.length-1,Math.ceil(durations.length*value)-1)]||0;
  await writeFile(new URL("result.json",output),JSON.stringify({
    passed:true,
    base:BASE,
    total:checks.length,
    passed_checks:checks.length,
    failed_checks:0,
    duration_ms:Date.now()-startedAt,
    p50_check_ms:percentile(0.5),
    p95_check_ms:percentile(0.95),
    checks,
    session_id:sid,
  },null,2));
} catch(error) {
  for (const [contextIndex,context] of contexts.entries()) {
    for (const [pageIndex,page] of context.pages().entries()) {
      await page.screenshot({path:new URL(`failure-${contextIndex}-${pageIndex}.png`,output).pathname,fullPage:true}).catch(()=>{});
    }
  }
  await writeFile(new URL("result.json",output),JSON.stringify({
    passed:false,
    base:BASE,
    total:checks.length+1,
    passed_checks:checks.length,
    failed_checks:1,
    duration_ms:Date.now()-startedAt,
    checks,
    error:String(error),
  },null,2));
  console.error(error);
  process.exitCode=1;
} finally {
  await Promise.all(contexts.map(c=>c.close()));
  await browser.close();
}
