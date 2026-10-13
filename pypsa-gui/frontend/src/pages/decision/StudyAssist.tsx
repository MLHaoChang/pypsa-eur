// U3: the decision panel's "Let the assistant do this" and "Ask about this"
// for one step, through the hub-design cards' own buttons (`delegate`,
// `ask`), so both Guided flows hand work over the same way. The copy and the
// sent text are the vocabulary's (`ASSIST_LABELS`, `assistText`).
import { AskButton, DelegateButton } from '../hubDesign/shared/CardShell'
import { ASSIST_LABELS, assistQuestion, assistText, type AssistStep } from '../../utils/decisionVocabulary'

export default function StudyAssist({ step, project, studyId }: {
  step: AssistStep; project: string; studyId: string
}) {
  const l = ASSIST_LABELS[step]
  return (
    <div data-testid={`study-assist-${step}`} className="flex flex-wrap items-center gap-2 pb-3">
      <DelegateButton testId={`study-delegate-${step}`} text={assistText(step, project, studyId)}
        display={l.delegate} label={l.delegate} />
      <AskButton testId={`study-ask-${step}`} text={assistQuestion(step)} label={l.ask} />
    </div>
  )
}
