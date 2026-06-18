type Props = {
  title: string;
  body: string;
  example?: string;
};

export function WizardStepHint({ title, body, example }: Props) {
  return (
    <div className="mb-4 space-y-1">
      <h2 className="text-lg font-bold text-white">{title}</h2>
      <p className="text-sm leading-7 text-white/65">{body}</p>
      {example && <p className="text-xs text-white/40">{example}</p>}
    </div>
  );
}
