(() => {
  const NAME = 'VincereArc'
  const TAGLINE = 'Trace the pace...Ace the paper'

  function rewriteText() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT)
    const nodes = []
    while (walker.nextNode()) nodes.push(walker.currentNode)
    for (const node of nodes) {
      if (node.nodeValue?.includes('AptoriQ')) {
        node.nodeValue = node.nodeValue.replaceAll('AptoriQ', NAME)
      }
      if (/Exam intelligence(?: platform)?/i.test(node.nodeValue || '')) {
        node.nodeValue = node.nodeValue.replace(/Exam intelligence(?: platform)?/gi, TAGLINE)
      }
      if (node.nodeValue?.includes('About us')) {
        node.nodeValue = node.nodeValue.replaceAll('About us', 'About')
      }
      if (node.nodeValue?.includes('curieexplorer@gmail.com')) {
        node.nodeValue = node.nodeValue.replaceAll('curieexplorer@gmail.com', '')
      }
    }
    const title = `${NAME} — ${TAGLINE}`
    if (document.title !== title) document.title = title
    const description = document.querySelector('meta[name="description"]')
    const content = `${NAME}: ${TAGLINE}`
    if (description && description.getAttribute('content') !== content) {
      description.setAttribute('content', content)
    }
  }

  function applyBranding() {
    const host = document.querySelector('header, nav')
    if (!host) return
    rewriteText()
    const main = document.querySelector('main')
    let contact = document.getElementById('contact')
    if (!contact && main) {
      contact = document.createElement('section')
      contact.id = 'contact'
      contact.style.cssText = 'margin:2rem auto 0;padding:3.5rem 2rem;max-width:80rem;border:1px solid #dfe4dc;border-radius:1.5rem;background:#f7f8f4;color:#182026;box-shadow:0 12px 30px rgba(24,32,38,.08)'
      contact.innerHTML = `
        <p style="margin:0;font-size:.7rem;font-weight:800;letter-spacing:.16em;text-transform:uppercase;color:#174f43">Contact</p>
        <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:1rem;max-width:52rem;margin-top:1.25rem">
          <a href="mailto:vojeswitha@vincerearc.in" style="display:flex;width:min(100%,24rem);justify-self:start;flex-direction:column;gap:.35rem;border:1px solid #dfe4dc;border-radius:1rem;background:#ffffff;padding:1.1rem 1.2rem;color:#174f43;text-align:left;text-decoration:none;transition:background .2s ease">
            <span style="font-size:1rem;font-weight:800">Vojeswitha Gopireddy</span>
            <span style="font-size:.82rem;color:#66736f">vojeswitha@vincerearc.in</span>
          </a>
          <a href="mailto:srijamuvva@vincerearc.in" style="display:flex;width:min(100%,24rem);justify-self:end;flex-direction:column;gap:.35rem;border:1px solid #dfe4dc;border-radius:1rem;background:#ffffff;padding:1.1rem 1.2rem;color:#174f43;text-align:left;text-decoration:none;transition:background .2s ease">
            <span style="font-size:1rem;font-weight:800">Srija Muvva</span>
            <span style="font-size:.82rem;color:#66736f">srijamuvva@vincerearc.in</span>
          </a>
        </div>
      `
      main.appendChild(contact)
    }
    for (const anchor of document.querySelectorAll('a')) {
      if (anchor.textContent?.trim() === 'Contact us') {
        anchor.href = '#contact'
        if (anchor.dataset.vincerearcContact !== 'true') {
          anchor.dataset.vincerearcContact = 'true'
          anchor.addEventListener('click', (event) => {
            event.preventDefault()
            contact?.scrollIntoView({ behavior: 'smooth', block: 'start' })
          })
        }
      }
    }
    for (const anchor of document.querySelectorAll('a[href*="curieexplorer"]')) {
      if (anchor.textContent?.trim() !== 'Contact us') anchor.remove()
    }
  }

  const observer = new MutationObserver(applyBranding)
  observer.observe(document.documentElement, { childList: true, subtree: true })
  window.setTimeout(applyBranding, 100)
})()
