import { expect, test, type APIRequestContext, type Page, type Request } from "@playwright/test";

test.setTimeout(60_000);

type Headers = Record<string, string>;

type Campaign = { id: string };

type KpSession = {
  access_token: string;
  member: { id: string };
};

type ModuleRun = {
  id: string;
  version: number;
  [key: string]: unknown;
};

async function postJson<T>(
  request: APIRequestContext,
  path: string,
  data: unknown,
  headers: Headers = {}
): Promise<T> {
  const response = await request.post(`/api${path}`, { data, headers });
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function getJson<T>(
  request: APIRequestContext,
  path: string,
  headers: Headers = {}
): Promise<T> {
  const response = await request.get(`/api${path}`, { headers });
  expect(response.ok(), `${path}: ${await response.text()}`).toBeTruthy();
  return response.json() as Promise<T>;
}

async function createKpCampaign(request: APIRequestContext, suffix: string) {
  const campaign = await postJson<Campaign>(request, "/campaigns", {
    title: `Need Help UI 验收 ${suffix}`,
    system: "coc7",
    current_time: "1928-10-04 21:00"
  });
  const session = await postJson<KpSession>(
    request,
    `/campaigns/${campaign.id}/sessions`,
    { kp_display_name: "新手 KP" }
  );
  return { campaign, session };
}

async function authenticateKp(page: Page, campaignId: string, accessToken: string) {
  await page.addInitScript(({ id, token }) => {
    sessionStorage.setItem(`ai-kp-session-token:${id}`, token);
  }, { id: campaignId, token: accessToken });
}

function isMutatingRequest(request: Request) {
  return ["POST", "PUT", "PATCH", "DELETE"].includes(request.method());
}

test("new KP recovers from a provider failure and receives source-visible read-only help", async ({
  page,
  request
}) => {
  const suffix = `${Date.now().toString(36)}-${test.info().workerIndex}`;
  const { campaign, session } = await createKpCampaign(request, suffix);
  const kpHeaders = { Authorization: `Bearer ${session.access_token}` };
  const module = await postJson<{ id: string }>(
    request,
    `/campaigns/${campaign.id}/modules`,
    {
      title: "湖畔旅馆调查",
      text: "@visibility=kp\n旅馆深夜仍有住客，调查员可以先确认点火位置和逃生计划。",
      source_type: "plaintext"
    },
    kpHeaders
  );
  const run = await postJson<ModuleRun>(
    request,
    `/campaigns/${campaign.id}/module-runs`,
    {
      module_id: module.id,
      current_scene_key: "motel-night",
      active_spoiler_tags: []
    },
    kpHeaders
  );

  const currentRunPath = `/campaigns/${campaign.id}/module-runs/current`;
  const factsPath = `/campaigns/${campaign.id}/facts`;
  const runBefore = await getJson<ModuleRun>(request, currentRunPath, kpHeaders);
  const factsBefore = await getJson<unknown[]>(request, factsPath, kpHeaders);
  const question = "玩家想烧掉旅馆，我该怎么办？";
  const submittedQuestions: string[] = [];
  let helpAttempts = 0;
  let currentRunRequests = 0;
  let historyRequests = 0;
  let auditItems: Array<Record<string, unknown>> = [];
  const unexpectedMutations: string[] = [];
  let watchMutations = false;

  page.on("request", (browserRequest) => {
    const pathname = new URL(browserRequest.url()).pathname;
    if (pathname === `/api${currentRunPath}`) currentRunRequests += 1;
    if (!watchMutations || !isMutatingRequest(browserRequest)) return;
    if (
      browserRequest.method() === "POST"
      && pathname === `/api/module-runs/${encodeURIComponent(run.id)}/director/help`
    ) return;
    unexpectedMutations.push(`${browserRequest.method()} ${pathname}`);
  });

  await page.route(
    `**/api/campaigns/${encodeURIComponent(campaign.id)}/director-help/audits**`,
    async (route) => {
      expect(route.request().method()).toBe("GET");
      const url = new URL(route.request().url());
      expect(url.searchParams.get("limit")).toBe("10");
      historyRequests += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: url.searchParams.has("before_id") ? [] : auditItems,
          next_before_id: null
        })
      });
    }
  );

  await page.route(
    `**/api/module-runs/${encodeURIComponent(run.id)}/director/help`,
    async (route) => {
      expect(route.request().method()).toBe("POST");
      const payload = route.request().postDataJSON() as { question?: unknown };
      expect(typeof payload.question).toBe("string");
      submittedQuestions.push(payload.question as string);
      helpAttempts += 1;

      if (helpAttempts === 1) {
        auditItems = [{
          id: "dhelp-ui-failed",
          run_id: run.id,
          requested_by_member_id: session.member.id,
          question,
          outcome: "failed",
          error_code: "upstream_service_error",
          duration_ms: 23,
          created_at: "2026-09-07T03:00:00Z",
          completed_at: "2026-09-07T03:00:00Z",
          response_hash: null,
          advice: null
        }];
        await route.fulfill({
          status: 502,
          contentType: "application/json",
          body: JSON.stringify({
            detail: "provider at http://127.0.0.1:8001 failed",
            code: "upstream_service_error"
          })
        });
        return;
      }

      const helpResponse = {
          run_id: run.id,
          run_version: run.version,
          contract_id: "servants-of-the-lake",
          contract_hash: "b".repeat(64),
          scenario_version: 3,
          state_version: 0,
          basis_hash: "c".repeat(64),
          question,
          status: "partial",
          answer: "先确认玩家准备在哪里点火、如何撤离，再根据当前契约决定是否进入检定。",
          follow_up_question: null,
          suggested_response: "先告诉我你准备在哪里点火，以及打算从哪条路撤离。",
          suggested_response_audience: "kp_review_only",
          next_steps: ["确认点火位置", "说明可见风险", "再选择契约允许的行动"],
          confidence: "medium",
          uncertainty_reasons: ["契约没有独立的纵火行动。"],
          assumptions: ["玩家尚未真正点火。"],
          citations: [{
            evidence_id: "operator:inspect-motel",
            source_type: "scenario_contract:operator",
            authority: "executable_contract",
            visibility: "kp",
            title: "汽车旅馆深夜",
            text: "旅馆深夜仍有住客，危险行动需要先明确方法。",
            source_locator: "湖之仆从 PDF p.61",
            source_refs: [{
              source_block_id: "page-61-motel",
              document_id: "lake-servants",
              page: 61,
              paragraph: 3
            }],
            source_refs_total_count: 1,
            source_refs_truncated: false
          }],
          action: {
            candidate_id: "inspect-motel",
            kind: "operator",
            title: "确认旅馆内外的危险条件",
            available: true,
            reason: "当前场景允许先调查环境。",
            policy: "required_check",
            selected_skill_key: "spot_hidden",
            step_operator_ids: [],
            skill_choices: [{
              skill_key: "spot_hidden",
              difficulty: "regular",
              reason: "确认火源附近的住客、出口与可燃物。",
              hidden: false,
              bonus_dice: 0,
              allow_push: false,
              scope: "旅馆公共区域",
              automatic_information: ["旅馆内仍有人。"],
              failure_stakes: "调查耗时并可能惊动住客。",
              pushed_failure_stakes: ""
            }],
            skill_choices_total_count: 1,
            skill_choices_truncated: false,
            automatic_information: ["烟雾会危及旅馆内的人。"],
            maximum_effect: "只提供裁定建议，不推进场景或写入事实。",
            success_effects: ["KP 可据此描述已确认的出口"],
            success_effects_total_count: 1,
            success_effects_truncated: false,
            failure_effects: ["KP 可说明调查耗时"],
            failure_effects_total_count: 1,
            failure_effects_truncated: false
          },
          writes_performed: false,
          can_execute: false
      };
      auditItems = [{
        id: "dhelp-ui-completed",
        run_id: run.id,
        requested_by_member_id: null,
        question,
        outcome: "completed",
        error_code: null,
        duration_ms: 37,
        created_at: "2026-09-07T03:01:00Z",
        completed_at: "2026-09-07T03:01:00Z",
        response_hash: "d".repeat(64),
        advice: helpResponse
      }, ...auditItems];
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(helpResponse)
      });
    }
  );

  await authenticateKp(page, campaign.id, session.access_token);
  await page.goto("/play");
  await expect(page.getByRole("button", { name: /需要帮助/ })).toBeVisible();
  await page.getByRole("button", { name: /需要帮助/ }).click();

  const questionInput = page.getByLabel("询问一个具体的带团问题");
  await questionInput.fill(`  ${question}  `);
  watchMutations = true;
  await page.getByRole("button", { name: "获取建议" }).click();

  const error = page.getByRole("alert");
  await expect(error).toContainText("暂时无法给出建议");
  await expect(error).toContainText("AI 服务暂时不可用，请稍后再试。");
  await expect(error).not.toContainText("127.0.0.1:8001");
  await expect(questionInput).toHaveValue(`  ${question}  `);
  await expect(questionInput).toBeEnabled();
  await expect(page.getByRole("button", { name: "获取建议" })).toBeEnabled();

  await page.getByRole("button", { name: "获取建议" }).click();

  const advice = page.getByRole("article", { name: "AI 带团建议" });
  await expect(advice).toBeVisible();
  await expect(advice).toContainText("给 KP 的措辞草稿");
  await expect(advice).toContainText("不可直接念给玩家");
  await expect(advice).toContainText("仅 KP");
  await expect(advice).toContainText("湖之仆从 PDF p.61");
  await expect(advice).toContainText("只读建议 · 未改变游戏状态");
  await expect(advice).toContainText("不可直接执行");
  await expect(advice.getByRole("button", { name: /执行|应用/ })).toHaveCount(0);

  const history = page.getByRole("list", { name: "Need Help 历史记录" });
  await expect(history).toBeVisible();
  const completedHistory = history.locator("li.outcome-completed");
  await expect(completedHistory).toContainText(question);
  await completedHistory.getByRole("button").click();
  const historicalAdvice = page.getByRole("article", { name: "历史 AI 带团建议" });
  await expect(historicalAdvice).toContainText("历史快照 · 基于当时状态 · 不可执行");
  await expect(historicalAdvice).toContainText("当时可用");
  await expect(historicalAdvice.getByRole("button", { name: /执行|应用/ })).toHaveCount(0);

  const helpTrigger = page.getByRole("button", { name: /需要帮助/ });
  await helpTrigger.click();
  await expect(history).not.toBeVisible();
  await helpTrigger.click();
  await expect(page.getByRole("list", { name: "Need Help 历史记录" })).toContainText(question);

  expect(unexpectedMutations).toEqual([]);
  watchMutations = false;
  await page.reload();
  await page.getByRole("button", { name: /需要帮助/ }).click();
  const restoredHistory = page.getByRole("list", { name: "Need Help 历史记录" });
  await expect(restoredHistory).toContainText(question);
  await expect(page.getByRole("article", { name: "AI 带团建议" })).toHaveCount(0);
  await restoredHistory.locator("li.outcome-completed").getByRole("button").click();
  await expect(page.getByRole("article", { name: "历史 AI 带团建议" })).toContainText(
    "湖之仆从 PDF p.61"
  );

  expect(submittedQuestions).toEqual([question, question]);
  expect(helpAttempts).toBe(2);
  expect(currentRunRequests).toBeGreaterThanOrEqual(2);
  expect(historyRequests).toBeGreaterThanOrEqual(4);

  const runAfter = await getJson<ModuleRun>(request, currentRunPath, kpHeaders);
  const factsAfter = await getJson<unknown[]>(request, factsPath, kpHeaders);
  expect(runAfter).toEqual(runBefore);
  expect(factsAfter).toEqual(factsBefore);
});

test("Need Help explains that no active module exists without posting a help request", async ({
  page,
  request
}) => {
  const suffix = `${Date.now().toString(36)}-${test.info().workerIndex}`;
  const { campaign, session } = await createKpCampaign(request, suffix);
  let helpPosts = 0;

  page.on("request", (browserRequest) => {
    if (
      browserRequest.method() === "POST"
      && /\/api\/module-runs\/[^/]+\/director\/help$/.test(
        new URL(browserRequest.url()).pathname
      )
    ) helpPosts += 1;
  });

  await authenticateKp(page, campaign.id, session.access_token);
  await page.goto("/play");
  await page.getByRole("button", { name: /需要帮助/ }).click();
  await page.getByLabel("询问一个具体的带团问题").fill("玩家突然离开现场怎么办？");
  await page.getByRole("button", { name: "获取建议" }).click();

  await expect(page.getByRole("alert")).toContainText("当前没有正在进行的模组。");
  expect(helpPosts).toBe(0);
});
