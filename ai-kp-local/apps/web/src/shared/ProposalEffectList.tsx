export function ProposalEffectList({
  title,
  items
}: {
  title: string;
  items: Record<string, unknown>[];
}) {
  return (
    <details className="effect-group" open={items.length > 0}>
      <summary>
        {title} <span>{items.length}</span>
      </summary>
      {items.length ? (
        <ul>
          {items.map((item, index) => (
            <li key={`${title}-${index}`}>{JSON.stringify(item, null, 2)}</li>
          ))}
        </ul>
      ) : (
        <small>无</small>
      )}
    </details>
  );
}
