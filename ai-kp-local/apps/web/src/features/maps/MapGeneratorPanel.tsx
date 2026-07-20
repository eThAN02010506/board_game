import { Map } from "lucide-react";
import type { FormEventHandler } from "react";

type Props = {
  title: string;
  prompt: string;
  locationsText: string;
  routesText: string;
  onTitleChange: (value: string) => void;
  onPromptChange: (value: string) => void;
  onLocationsTextChange: (value: string) => void;
  onRoutesTextChange: (value: string) => void;
  onGenerate: FormEventHandler<HTMLFormElement>;
};

export function MapGeneratorPanel(props: Props) {
  return (
    <section className="tool-panel map-panel">
      <div className="panel-heading">
        <h2>地图生成与保存</h2>
        <Map size={18} />
      </div>
      <form className="map-form" onSubmit={props.onGenerate}>
        <label>
          地图名
          <input value={props.title} onChange={(event) => props.onTitleChange(event.target.value)} />
        </label>
        <label>
          场景描述
          <textarea
            value={props.prompt}
            onChange={(event) => props.onPromptChange(event.target.value)}
          />
        </label>
        <label>
          地点
          <textarea
            value={props.locationsText}
            onChange={(event) => props.onLocationsTextChange(event.target.value)}
          />
        </label>
        <label>
          路线
          <textarea
            value={props.routesText}
            onChange={(event) => props.onRoutesTextChange(event.target.value)}
          />
        </label>
        <button className="primary-button" type="submit">
          <Map size={16} />
          生成地图
        </button>
      </form>
    </section>
  );
}
