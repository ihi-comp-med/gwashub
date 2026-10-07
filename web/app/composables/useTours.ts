/** Runs a guided tour (utils/tours.ts) with driver.js: one step at a time, moving between pages as the steps say.
 * A step whose element isn't on the page yet either waits (the popover says what to do first) or, if optional, is
 * skipped. Elements are found by their data-tour attribute. */
import { driver, type Driver } from 'driver.js'
import 'driver.js/dist/driver.css'

let d: Driver | null = null

function visible(el: Element | null): el is HTMLElement {
  return !!el && (el as HTMLElement).getClientRects().length > 0
}

async function find(key: string, ms: number): Promise<HTMLElement | null> {
  const deadline = Date.now() + ms
  for (;;) {
    const el = [...document.querySelectorAll(`[data-tour="${key}"]`)].find(visible) as HTMLElement | undefined
    if (el || Date.now() > deadline)
      return el ?? null
    await new Promise(r => setTimeout(r, 120))
  }
}

export function useTours() {
  const route = useRoute()
  const router = useRouter()

  function stop() {
    d?.destroy()
    d = null
  }

  function start(tour: Tour, pid: string | null) {
    stop()
    d = driver({ popoverClass: 'gh-tour', stagePadding: 6, stageRadius: 8, allowClose: true, smoothScroll: true,
      overlayOpacity: 0.45, onDestroyed: () => { d = null } })
    let current = -1
    let currentEl: HTMLElement | null = null
    const n = tour.steps.length

    function render(i: number, el: HTMLElement | null, hint?: string) {
      const s = tour.steps[i]!
      current = i
      currentEl = el
      const last = i === n - 1
      d?.highlight({
        element: el ?? undefined,
        popover: {
          title: s.title,
          description: s.text + (hint ? `<p class="gh-tour-wait"><i class="pi pi-info-circle"></i> ${hint}</p>` : ''),
          showButtons: i > 0 ? ['next', 'previous', 'close'] : ['next', 'close'],
          showProgress: true, progressText: `${i + 1} of ${n}`,
          nextBtnText: last ? 'Done' : 'Next', prevBtnText: 'Back',
          onNextClick: () => { last ? stop() : go(i + 1, 1) },
          onPrevClick: () => { go(i - 1, -1) },
          onCloseClick: () => stop(),
        },
      })
    }

    async function go(i: number, dir: 1 | -1) {
      if (i < 0 || i >= n || !d)
        return
      const s = tour.steps[i]!
      let moved = false
      if (s.page) {
        const path = s.page.replace(':pid', pid ?? '')
        if (route.path !== path) {
          await router.push(path)
          moved = true
        }
      }
      if (!s.el)
        return render(i, null)
      const el = await find(s.el, moved ? 6000 : s.wait ? 900 : 2500)
      if (el) {
        el.scrollIntoView({ block: 'center', behavior: 'smooth' })
        await new Promise(r => setTimeout(r, 250))
        return render(i, el)
      }
      if (s.optional && i + dir >= 0 && i + dir < n)
        return go(i + dir, dir)
      if (current >= 0 && dir === 1)  // stay where we are and say what to do first
        return render(current, currentEl, s.wait ?? 'That part of the page isn\'t showing yet.')
      return render(i, null, s.wait)
    }

    go(0, 1)
  }

  return { start, stop }
}
