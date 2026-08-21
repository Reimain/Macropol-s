import { Flight } from "./Flight";

export default function App() {
  return (
    <main>
      <header>
        <h1>SLPIE — enterprise console</h1>
        <p className="muted">
          The screens the stdlib console names and declines to draw. It runs
          air-gapped; this one needs a toolchain, and both say which they are.
        </p>
      </header>
      <Flight />
    </main>
  );
}
