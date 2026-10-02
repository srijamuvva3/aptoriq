(() => {
  const NAME = 'VincereArc'
  const TAGLINE = 'Trace the pace...Ace the paper'

  function applyBranding() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    const nodes = []

    while (walker.nextNode()) nodes.push(walker.currentNode)

    for (const node of nodes) {
      if (!node.nodeValue) continue

      node.nodeValue = node.nodeValue
        .replaceAll('AptoriQ', NAME)
        .replace(/Exam intelligence(?: platform)?/gi, TAGLINE)
    }

    const title = `${NAME} — ${TAGLINE}`
    if (document.title !== title) document.title = title

    const description = document.querySelector('meta[name="description"]')
    const content = `${NAME}: ${TAGLINE}`

    if (description && description.getAttribute('content') !== content) {
      description.setAttribute('content', content)
    }
  }

  const observer = new MutationObserver(applyBranding)
  observer.observe(document.documentElement, {
    childList: true,
    subtree: true,
  })

  window.setTimeout(applyBranding, 100)
})()
