/* The shell, and the one question it asks before drawing anything.
 *
 * It does not carry a list of its own screens. It asks `GET /api/shells` which
 * screens the **stdlib console cannot draw**, and renders those — the platform
 * decides what this shell is for, from the capabilities each screen declares
 * against the capabilities each shell provides.
 *
 * That inversion is the whole design. A hand-written list here would be a
 * second statement of the same truth, and the moment a new screen needed
 * `virtual-scroll` the air-gapped console would quietly stop mentioning it
 * while this one quietly failed to add it. Instead the kernel's `cannot` map
 * *is* the backlog, and a screen this shell has not built yet says so on screen
 * rather than being absent.
 *
 * The reverse case is handled too, and it is the honest one: a screen neither
 * shell can draw renders the refusal card with the missing capability named. A
 * console that silently omitted it would be hiding a capability the platform
 * has — which is the drift §24 exists to prevent.
 */

import { client } from "./api";
import { useApi } from "./useApi";
import { Refusal } from "./ui/Refusal";
import { SCREENS } from "./screens";

type ShellRow = {
  name: string;
  title: string;
  provides?: string[];
  native?: boolean;
  built?: boolean;
  summary?: string;
  renders?: string[];
  cannot?: Record<string, string[]>;
};

type ShellsPayload = { capabilities?: Record<string, string>; shells?: ShellRow[] };

/** The shell whose gaps are this shell's reason to exist. */
const AIR_GAPPED = "stdlib";
const HERE = "web";

export default function App() {
  const answer = useApi<ShellsPayload>(() => client.readShells(), []);

  const shells = answer.value?.shells || [];
  const capabilities = answer.value?.capabilities || {};
  const declined = shells.find((row) => row.name === AIR_GAPPED)?.cannot || {};
  const ours = shells.find((row) => row.name === HERE);
  // Sorted, so the order is the platform's and not the JSON's.
  const keys = Object.keys(declined).sort();

  return (
    <main>
      <header>
        <h1>SLPIE — enterprise console</h1>
        <p className="muted">
          The screens the stdlib console names and declines to draw. It runs
          air-gapped; this one needs a toolchain, and both say which they are.
        </p>
      </header>

      {answer.loading && <p className="muted pad">Asking the platform which screens it declines…</p>}

      {answer.error && (
        <div className="pad">
          <Refusal answer={answer} subject="The shell manifest" />
          <p className="muted small">
            Nothing is drawn rather than a guess at what belongs here.
          </p>
        </div>
      )}

      {answer.value && keys.length === 0 && (
        <p className="muted pad">
          The stdlib console can draw every screen the platform declares. There
          is nothing for this shell to add, which is the good outcome — the one
          that runs air-gapped is enough.
        </p>
      )}

      {keys.map((key) => {
        const Screen = SCREENS[key];
        const missingHere = ours?.cannot?.[key];

        if (missingHere) {
          // Neither shell can draw it. The refusal card in reverse: the
          // platform has the capability and no surface can reach it, and
          // saying so is the only honest rendering.
          return (
            <section key={key} className="pad">
              <h2>{key}</h2>
              <p className="refusal">
                Neither console can draw this screen. It needs{" "}
                {missingHere.map((need) => capabilities[need] || need).join(", ")}
                {" "}— a capability the platform has and no surface reaches.
              </p>
            </section>
          );
        }

        if (!Screen) {
          return (
            <section key={key} className="pad">
              <h2>{key}</h2>
              <p className="muted">
                The stdlib console declines this screen because it needs{" "}
                {(declined[key] || []).map((need) => capabilities[need] || need).join(", ")}.
                This shell can draw it and has not built it yet — declared here
                rather than quietly missing from both.
              </p>
            </section>
          );
        }

        return <Screen key={key} />;
      })}
    </main>
  );
}
