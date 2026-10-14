import BuildSection from './BuildSection'
import TimeSection from './TimeSection'
import EconomicsSection from './EconomicsSection'

// MODEL (owner decision Q6a): everything that describes the system before a
// run — its assets and build limits, its time horizon and series, and its
// economics — in the order they are usually set.
export default function ModelSection({ onCloseModal }: { onCloseModal?: () => void }) {
  return (
    <>
      <BuildSection onCloseModal={onCloseModal} />
      <TimeSection onCloseModal={onCloseModal} />
      <EconomicsSection onCloseModal={onCloseModal} />
    </>
  )
}
