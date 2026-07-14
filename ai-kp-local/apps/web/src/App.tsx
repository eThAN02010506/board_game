import { Brain, Map, MessageSquare, Users } from "lucide-react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const panels = [
  { title: "KP 回合", icon: MessageSquare, text: "玩家行动、AI 反馈、检定请求和人类 KP 覆盖。" },
  { title: "长期记忆", icon: Brain, text: "角色主线/支线、NPC 往事、跨本可回忆事实。" },
  { title: "NPC 档案", icon: Users, text: "关系、地点、职业、秘密信息与再次出现预算。" },
  { title: "地图路线", icon: Map, text: "地点图、队伍分流、时间推进和冲突汇合。" }
];

function App() {
  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">AI KP Local</div>
        <button className="nav-item active">雾港 1928</button>
        <button className="nav-item">角色卡</button>
        <button className="nav-item">NPC</button>
        <button className="nav-item">记忆审计</button>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <p className="eyebrow">本地跑团工作台</p>
            <h1>雾港 1928</h1>
          </div>
          <button className="primary-button">开始 KP 回合</button>
        </header>

        <div className="grid">
          {panels.map((panel) => {
            const Icon = panel.icon;
            return (
              <article className="panel" key={panel.title}>
                <Icon size={22} />
                <h2>{panel.title}</h2>
                <p>{panel.text}</p>
              </article>
            );
          })}
        </div>

        <section className="turn-console">
          <div className="console-header">
            <h2>行动输入</h2>
            <span>Human KP: standby</span>
          </div>
          <textarea placeholder="例如：我想找旧码头认识、行业内打过交道的人。" />
          <div className="console-actions">
            <button>只检索记忆</button>
            <button className="primary-button">提交给 AI KP</button>
          </div>
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);

