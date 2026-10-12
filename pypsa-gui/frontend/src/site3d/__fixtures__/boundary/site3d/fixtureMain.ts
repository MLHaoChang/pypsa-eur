// Fixture: a main-bundle module that (wrongly) imports a SiteCanvas-only one.
import { fixtureChain } from './fixtureChain'
export const fixtureMain = () => fixtureChain()
