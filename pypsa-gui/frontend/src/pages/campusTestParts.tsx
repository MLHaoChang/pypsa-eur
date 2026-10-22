// Test helper: the three panels the campus study is split over, mounted
// together, for suites that exercise the study end to end.
import CampusElectricalPanel, { CampusResultsPanel, CampusStudyPanel } from './CampusElectricalPanel'

export default function CampusTestParts() {
  return (<><CampusElectricalPanel /><CampusStudyPanel /><CampusResultsPanel /></>)
}
