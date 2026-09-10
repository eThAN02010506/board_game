import { expect, type Page } from "@playwright/test";

export type PlayerPersona = {
  id: string;
  intents: readonly string[];
  clarification: string;
  pushPolicy: "accept_failure" | "push_once";
};

export type PlayerTurnTrace = {
  result: "resolved" | "story_completed";
  clarification_count: number;
  confirmation_count: number;
  digital_roll_count: number;
  push_count: number;
  accepted_failure_count: number;
  observed_routes: string[];
  selected_skills: string[];
};

function newTurnTrace(): Omit<PlayerTurnTrace, "result"> {
  return {
    clarification_count: 0,
    confirmation_count: 0,
    digital_roll_count: 0,
    push_count: 0,
    accepted_failure_count: 0,
    observed_routes: [],
    selected_skills: []
  };
}

export const REAL_PLAYER_PERSONAS: readonly PlayerPersona[] = [
  {
    id: "trpg_newcomer",
    intents: [
      "我只做目视观察：查看当前场景里与委托直接相关的公开记录、物件或痕迹，优先选其中一个；我不触碰或移动它，只想确认一处肉眼可见的不一致，若不存在就结束行动。",
      "我把已经公开的线索按时间排序，只核对其中一项与当前场景是否矛盾；不新增事实，找不到就结束行动。"
    ],
    clarification: "我会从可见痕迹、现场关系和已知时间顺序入手，只验证当前这一个判断。",
    pushPolicy: "accept_failure"
  },
  {
    id: "roleplayer",
    intents: [
      "我对当前场景里愿意交流的人说：‘您好，我们是来处理眼前委托的。请从头说明最近一次异常发生的时间、地点和您亲眼确认的事，可以吗？’我只听回答，不预设他说谎。",
      "我复述当前人物刚才公开说过的一句话，然后问：‘这件事发生前后，您还亲眼看见了什么？’我等待具体回答。"
    ],
    clarification: "我会先说出具体台词，再根据对方当下态度决定是否继续追问，不预设他说谎。",
    pushPolicy: "push_once"
  },
  {
    id: "rules_veteran",
    intents: [
      "我先检查当前场景中与委托直接相关、肉眼可见的一个物件或记录；只做不拆卸的外观检查，目标是确认其状态，十分钟内没有结果就停止。需要检定时请列出合理技能和失败代价让我确认。",
      "我先确认当前目标的一项具体风险和退路，再用角色卡上真正拥有的能力尝试一个可逆步骤；若现场条件不足就不执行。"
    ],
    clarification: "我只要求本次方法能达到的有限效果；如果需要检定，请让我确认技能与失败代价。",
    pushPolicy: "accept_failure"
  },
  {
    id: "chaos_sandbox_player",
    intents: [
      "我在当前场景只寻找一名已经公开、愿意交流的当地人，提出用角色现有的小额现金请他在一小时内提供一条可核实的消息来源，不要求结论；没人愿意就结束行动并自己调查。",
      "我只用角色卡已有物品和当前公开可用的普通物件，尝试一个可撤销的临时方案；目标限于解决眼前一个障碍，缺少任何条件就停止。"
    ],
    clarification: "我会说明资源从哪里来、请谁帮忙、要付什么代价；条件不够时先完成准备步骤。",
    pushPolicy: "push_once"
  },
  {
    id: "optimizer",
    intents: [
      "我先核对角色卡上最适合当前公开目标的能力，再提出一个只使用现场已有条件、十分钟内可停止的实际方法；请列出合理技能、难度和失败代价，由我确认。",
      "我询问当前难度、奖励或惩罚因素和失败代价，再决定是否执行这个高收益方案。"
    ],
    clarification: "请把可选技能、目标值、难度与可见风险列出；最终使用哪项技能仍由我确认。",
    pushPolicy: "push_once"
  }
];

function finalClarificationAnswer(persona: PlayerPersona): string {
  switch (persona.id) {
    case "trpg_newcomer":
      return "对象仅限当前场景中与委托直接相关、肉眼可见的一份记录或一处痕迹；方法只有目视比较，不触碰、不移动，目标只是一条可观察差异，最多十分钟；没有对象或差异就结束本次行动。";
    case "roleplayer":
      return "对象仅限当前场景中已经公开且愿意交流的一人；我的原话是：‘请只说您亲眼确认的最近一次异常及发生时间。’我不要求秘密、不判断真假，对方拒绝就结束谈话。";
    case "rules_veteran":
      return "对象是当前场景中与委托直接相关的一件可见物或记录；只做外观检查，不拆卸、不消耗物品，最多十分钟，目标只是确认当前状态；若需要检定，请从角色实际技能中给出选项，由我手动确认。";
    case "chaos_sandbox_player":
      return "对象仅限当前场景中已公开、愿意交流的一名当地人；资源仅限角色已有的小额现金，成本上限是一小时和这笔现金；效果上限是一条可核实的来源，不获得结论、不创造身份；无人愿意就结束行动。";
    default:
      return "只执行当前原行动：使用角色卡已有能力和当前场景公开条件，最多十分钟，只取得一项可验证结果；不新增身份、资源或后续步骤，条件不足就结束。需要检定时由我确认技能与失败代价。";
  }
}

export function actionForPersona(persona: PlayerPersona, turnIndex: number) {
  return persona.intents[turnIndex % persona.intents.length];
}

async function visible(page: Page, role: "button" | "tab", name: string | RegExp) {
  return page.getByRole(role, { name }).first().isVisible().catch(() => false);
}

export async function playerActionSurfaceDiagnostics(page: Page) {
  const confirm = page.getByRole("button", { name: "确认此裁定" }).first();
  const revise = page.getByRole("button", { name: "修改行动并重新裁定" }).first();
  return {
    url: page.url(),
    action_tab_selected: await page.getByRole("tab", { name: "行动" })
      .getAttribute("aria-selected").catch(() => null),
    ruling_card_count: await page.locator(".action-ruling-card").count(),
    confirm_count: await page.getByRole("button", { name: "确认此裁定" }).count(),
    confirm_disabled: await confirm.isDisabled().catch(() => null),
    revise_count: await page.getByRole("button", { name: "修改行动并重新裁定" }).count(),
    revise_disabled: await revise.isDisabled().catch(() => null),
    batch_status: await page.locator("[aria-label='多人并行动作状态']")
      .textContent().catch(() => null),
    auto_kp_status: await page.locator(".auto-kp-player-status")
      .textContent().catch(() => null)
  };
}

async function resolveVisibleChecks(
  page: Page,
  persona: PlayerPersona,
  pushed: Set<string>,
  trace: Omit<PlayerTurnTrace, "result">
) {
  if (await visible(page, "tab", "检定")) {
    await page.getByRole("tab", { name: "检定" }).click({ timeout: 10_000 });
  }
  const requested = page.locator(".check-card.requested");
  for (let index = 0; index < await requested.count(); index += 1) {
    const card = requested.nth(index);
    const digital = card.getByRole("button", { name: "数字骰" });
    if (await digital.isVisible().catch(() => false)) {
      await digital.click({ timeout: 10_000 });
      trace.digital_roll_count += 1;
    }
  }

  const failed = page.locator(".check-card.failed");
  for (let index = 0; index < await failed.count(); index += 1) {
    const card = failed.nth(index);
    const accept = card.getByRole("button", { name: "接受普通失败" });
    if (!await accept.isVisible().catch(() => false)) continue;
    const cardKey = await card.textContent() ?? `check-${index}`;
    const shouldPush = persona.pushPolicy === "push_once" && !pushed.has(cardKey);
    if (shouldPush) {
      pushed.add(cardKey);
      await card.getByLabel("若要推动，请说明如何改变做法").fill(
        "我改变方法并承担更大风险：缩小目标，只用现场已经确认可用的条件再试一次。"
      );
      await card.getByRole("button", { name: "创建孤注一掷" }).click({ timeout: 10_000 });
      trace.push_count += 1;
    } else {
      await accept.click({ timeout: 10_000 });
      trace.accepted_failure_count += 1;
    }
  }
}

export async function submitPlayerAction(
  page: Page,
  persona: PlayerPersona,
  turnIndex: number,
  actionOverride?: string
) {
  const action = actionOverride ?? actionForPersona(persona, turnIndex);
  const actionInput = page.getByLabel("玩家行动");
  await expect(actionInput).toBeEnabled();
  await actionInput.fill(action);
  const autoAdvance = page.getByLabel("AI KP 自动推进");
  await expect(autoAdvance).toBeEnabled({ timeout: 30_000 });
  if (!await autoAdvance.isChecked()) await autoAdvance.check({ timeout: 30_000 });
  // The accessible label changes as soon as auto advance is enabled. Using the
  // manual-only label here made Playwright wait forever because actionTimeout is
  // intentionally unbounded for long model runs.
  const submitted = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && /\/campaigns\/[^/]+\/actions$/.test(new URL(response.url()).pathname)
  );
  await page.getByRole("button", {
    name: /提交并后台推进|提交给 KP/
  }).click({ timeout: 30_000 });
  const response = await submitted;
  if (!response.ok()) {
    throw new Error(`player action submission failed: ${response.status()} ${await response.text()}`);
  }
  await response.finished();
}

export async function settleSubmittedPlayerAction(
  page: Page,
  persona: PlayerPersona,
  turnIndex: number,
  options: { timeoutMs?: number; actionOverride?: string } = {}
): Promise<PlayerTurnTrace> {
  const timeoutMs = options.timeoutMs ?? 360_000;
  const action = options.actionOverride ?? actionForPersona(persona, turnIndex);
  const actionInput = page.getByLabel("玩家行动");
  const deadline = Date.now() + timeoutMs;
  const pushed = new Set<string>();
  const trace = newTurnTrace();
  const initialPublicTurnCount = await page
    .locator(".public-turn-card:not(.story-opening-card)").count();
  let sawProcessing = false;
  let revisions = 0;
  let confirmedRuling = false;
  while (Date.now() < deadline) {
    if (await page.getByLabel("本次故事已结束").isVisible().catch(() => false)) {
      return { ...trace, result: "story_completed" };
    }
    if (!confirmedRuling && await visible(page, "button", "确认此裁定")) {
      sawProcessing = true;
      console.log(`[PLAYER-TURN] ${persona.id} confirmation visible`);
      const ruling = page.locator(".action-ruling-card");
      const route = await ruling.locator(".tabletop-turn-frame strong").textContent().catch(() => null);
      if (route?.trim() && !trace.observed_routes.includes(route.trim())) {
        trace.observed_routes.push(route.trim());
      }
      const selectedSkill = await ruling.locator("select option:checked")
        .textContent().catch(() => null);
      if (selectedSkill?.trim() && !trace.selected_skills.includes(selectedSkill.trim())) {
        trace.selected_skills.push(selectedSkill.trim());
      }
      const confirm = page.getByRole("button", { name: "确认此裁定" });
      await expect(confirm).toBeEnabled({ timeout: 30_000 });
      console.log(`[PLAYER-TURN] ${persona.id} confirmation enabled`);
      await confirm.click({ timeout: 30_000 });
      confirmedRuling = true;
      trace.confirmation_count += 1;
      console.log(`[PLAYER-TURN] ${persona.id} confirmation clicked`);
    } else if (!confirmedRuling && await visible(page, "button", "修改行动并重新裁定")) {
      sawProcessing = true;
      if (revisions >= 3) {
        throw new Error(`${persona.id} remained in clarification after three concrete revisions`);
      }
      revisions += 1;
      trace.clarification_count += 1;
      const kpQuestion = await page.locator(".rp-request-detail").textContent().catch(() => "");
      const boundedAnswer = revisions === 1
        ? persona.clarification
        : finalClarificationAnswer(persona);
      await actionInput.fill(
        `${action} KP追问：${kpQuestion ?? ""} 我的具体回答：${boundedAnswer}`
      );
      const revise = page.getByRole("button", { name: "修改行动并重新裁定" });
      await expect(revise).toBeEnabled({ timeout: 30_000 });
      await revise.click({ timeout: 30_000 });
      confirmedRuling = false;
    }

    await resolveVisibleChecks(page, persona, pushed, trace);
    const hasRequestedCheck = await page.locator(".check-card.requested").count() > 0;
    const hasPushDecision = await page.getByRole("button", {
      name: /接受普通失败|创建孤注一掷/
    }).first().isVisible().catch(() => false);
    if (!hasRequestedCheck && !hasPushDecision && await visible(page, "tab", "行动")) {
      await page.getByRole("tab", { name: "行动" }).click({ timeout: 10_000 });
    }
    if (await page.getByLabel("本次故事已结束").isVisible().catch(() => false)) {
      return { ...trace, result: "story_completed" };
    }
    const publicTurnCount = await page
      .locator(".public-turn-card:not(.story-opening-card)").count();
    if (publicTurnCount > initialPublicTurnCount) {
      return { ...trace, result: "resolved" };
    }
    if (await page.locator(".auto-kp-player-status, [aria-label='多人并行动作状态']").isVisible().catch(() => false)) {
      sawProcessing = true;
    }
    if (sawProcessing && await actionInput.isEnabled().catch(() => false)) {
      await page.getByRole("tab", { name: "行动" }).click({ timeout: 10_000 }).catch(() => undefined);
      if (!await page.locator(".auto-kp-player-status, [aria-label='多人并行动作状态']").isVisible().catch(() => false)) {
        continue;
      }
    }
    await page.waitForTimeout(500);
  }
  const diagnostics = await playerActionSurfaceDiagnostics(page);
  throw new Error(
    `${persona.id} did not reach a terminal player-visible state within ${timeoutMs}ms: ${JSON.stringify(diagnostics)}`
  );
}

export async function playPlayerTurn(
  page: Page,
  persona: PlayerPersona,
  turnIndex: number,
  options: { timeoutMs?: number; actionOverride?: string } = {}
): Promise<PlayerTurnTrace> {
  await submitPlayerAction(page, persona, turnIndex, options.actionOverride);
  return settleSubmittedPlayerAction(page, persona, turnIndex, options);
}
