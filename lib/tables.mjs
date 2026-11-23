// Audit tables. Everything keyword-based lives here.

// SECTION_KEYWORDS: heading text -> normalised section label.
// Matching: heading text is lower-cased and whitespace-collapsed; rows are tried
// top to bottom and the FIRST row with any matching pattern wins. Unmatched -> 'other'.
// Order matters (e.g. 'how it works' must beat 'get started', 'pricing' must beat 'cta').
export const SECTION_KEYWORDS = [
  ['faq', [/\bfaqs?\b/, /frequently asked/, /\bquestions\b/, /\bq ?& ?a\b/, /\bgot questions/]],
  ['pricing', [/\bpric(e|es|ing)\b/, /\bplans?\b/, /\bmembership/, /\brates\b/, /\bpackages?\b/, /\btiers?\b/, /\bsubscription/]],
  ['testimonials', [/testimonial/, /\breviews?\b/, /\bwhat (our |people |they )?\w* ?(say|are saying|think)/, /\bloved by\b/, /\bkind words\b/, /\bpraise\b/, /\bstories\b/, /\bdon'?t (just )?take our word/, /\bin their words/]],
  ['how-it-works', [/how (it|this|we) works?/, /how does it work/, /\bsteps?\b/, /\bprocess\b/, /\bworkflow\b/, /get started in/, /\bhow to\b/]],
  ['menu', [/\bmenu\b/, /\bdishes\b/, /\bour food\b/, /\bdrinks\b/, /\bcuisine\b/, /\bspecials\b/, /\bwine list\b/, /\bfrom (our|the) kitchen/]],
  ['services/practice-areas', [/\bservices?\b/, /practice areas?/, /areas? of (practice|expertise|law)/, /\bwhat we do\b/, /\bexpertise\b/, /\bofferings?\b/, /\bspecialt(y|ies)\b/, /\btreatments?\b/, /\bwe can help\b/]],
  ['gallery/portfolio', [/\bgallery\b/, /\bportfolio\b/, /\bour work\b/, /\bselected work\b/, /\bprojects?\b/, /case stud/, /\bshowcase\b/]],
  ['team', [/\bteam\b/, /\bmeet (the|our)\b/, /\bour people\b/, /\bstaff\b/, /\bfounders?\b/, /\bleadership\b/, /\battorneys\b/, /\blawyers\b/, /\bchefs?\b/]],
  ['about', [/\babout\b/, /\bour story\b/, /\bwho we are\b/, /\bmission\b/, /\bhistory\b/, /\bphilosophy\b/, /\bvalues\b/, /\bour approach\b/, /\bsince \d{4}\b/]],
  ['hours/location', [/\bhours\b/, /\blocations?\b/, /\bvisit us\b/, /\bfind us\b/, /\bopening\b/, /\bdirections\b/, /\bwhere to find/]],
  ['contact', [/\bcontact\b/, /get in touch/, /\breach (us|out)\b/, /\btalk to (us|an?)\b/, /\bconsultation\b/, /\bsay hello\b/, /\bdrop us a line\b/]],
  ['features', [/\bfeatures?\b/, /\bwhy (choose|us|teams|people|\w+ choose)\b/, /\bbenefits?\b/, /\bwhat you get\b/, /\bcapabilit/, /everything you need/, /\bbuilt for\b/, /\bpowerful\b/, /\bwhy \w+\??$/, /^why\b/]],
  ['cta', [/\bget started\b/, /\bstart (your |a )?(free )?trial\b/, /\bready to\b/, /\bjoin\b/, /\bsign up\b/, /\btry (it|\w+ )?(free|now|today)/, /\bbook (a|your|now)\b/, /\breserve\b/, /\breservations?\b/, /\bsubscribe\b/, /\bnewsletter\b/, /\bdownload\b/, /\brequest a demo\b/, /\bstart (building|today|now)\b/]],
];
// 'hero' and 'footer' are structural, not keyword-based: see README (section_order_norm).

export function normaliseSection(text) {
  const t = String(text || '').toLowerCase().replace(/\s+/g, ' ').trim();
  for (const [label, pats] of SECTION_KEYWORDS) if (pats.some((p) => p.test(t))) return label;
  return 'other';
}

// FRAMEWORKS: tested against every <link href>, <script src>, @import URL and
// every URL the page actually requested during load. A label is reported if any
// pattern matches any of those URLs.
export const FRAMEWORKS = [
  ['tailwind-cdn', [/cdn\.tailwindcss\.com/, /@tailwindcss\/browser/, /tailwind(css)?(@[\d.]+)?\/dist/, /tailwind(\.min)?\.css/]],
  ['bootstrap', [/bootstrap(@[\d.]+)?\/dist/, /bootstrap(\.bundle)?(\.min)?\.(css|js)/, /\/bootstrap\/[\d.]+\//]],
  ['bootstrap-icons', [/bootstrap-icons/]],
  ['bulma', [/bulma/]],
  ['foundation', [/foundation(\.min)?\.(css|js)/, /foundation-sites/]],
  ['pico', [/picocss|@picocss|pico(\.min)?\.css/]],
  ['daisyui', [/daisyui/]],
  ['flowbite', [/flowbite/]],
  ['materialize', [/materialize/]],
  ['uikit', [/uikit/]],
  ['font-awesome', [/font-?awesome/, /kit\.fontawesome\.com/]],
  ['google-fonts', [/fonts\.googleapis\.com\/css/, /fonts\.gstatic\.com/]],
  ['material-icons', [/fonts\.googleapis\.com\/icon/, /material-icons/, /material-symbols/, /Material\+(Icons|Symbols)/]],
  ['bunny-fonts', [/fonts\.bunny\.net/]],
  ['fontshare', [/fontshare\.com/]],
  ['adobe-fonts', [/use\.typekit\.net/, /p\.typekit\.net/]],
  ['lucide', [/lucide/]],
  ['feather-icons', [/feather-icons|feather(\.min)?\.js/]],
  ['heroicons', [/heroicons/]],
  ['ionicons', [/ionicons/]],
  ['remixicon', [/remixicon/]],
  ['phosphor-icons', [/phosphor-icons|@phosphor-icons/]],
  ['iconify', [/iconify/]],
  ['alpine', [/alpinejs/]],
  ['htmx', [/htmx/]],
  ['jquery', [/jquery/]],
  ['react', [/\breact(-dom)?(@[\d.]+)?\//, /react(-dom)?(\.production)?(\.min)?\.js/]],
  ['vue', [/\bvue(@[\d.]+)?\//, /vue(\.global)?(\.prod)?(\.min)?\.js/]],
  ['gsap', [/gsap/]],
  ['aos', [/\baos(@[\d.]+)?\//, /aos(\.min)?\.(css|js)/]],
  ['animate.css', [/animate(\.min)?\.css/]],
  ['three.js', [/three(\.module)?(\.min)?\.js/, /\bthree(@[\d.]+)?\//]],
  ['chart.js', [/chart(\.umd)?(\.min)?\.js/, /chart\.js/]],
  ['swiper', [/swiper/]],
  ['lottie', [/lottie/]],
  ['particles', [/particles(\.min)?\.js|tsparticles/]],
  ['unsplash-images', [/images\.unsplash\.com|source\.unsplash\.com/]],
  ['picsum-images', [/picsum\.photos/]],
  ['placeholder-images', [/placehold\.co|placeholder\.com|via\.placeholder|dummyimage\.com|placekitten|loremflickr|pravatar\.cc|randomuser\.me|ui-avatars\.com/]],
];

// Generic CDN hosts: reported as 'cdn:<host>' when any requested/linked URL uses them.
export const CDN_HOSTS = ['unpkg.com', 'cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'cdn.skypack.dev', 'esm.sh', 'ga.jspm.io', 'cdn.tailwindcss.com', 'code.jquery.com', 'stackpath.bootstrapcdn.com', 'maxcdn.bootstrapcdn.com', 'ajax.googleapis.com'];

// Hosts treated as font hosts for external_font_hosts (in addition to any host
// that served a request of resourceType 'font').
export const FONT_CSS_HOSTS = ['fonts.googleapis.com', 'fonts.gstatic.com', 'fonts.bunny.net', 'api.fontshare.com', 'cdn.fontshare.com', 'use.typekit.net', 'p.typekit.net', 'rsms.me', 'fonts.cdnfonts.com'];

export function detectFrameworks(urls) {
  const found = new Set();
  for (const u of urls) {
    for (const [label, pats] of FRAMEWORKS) if (pats.some((p) => p.test(u))) found.add(label);
    let host = '';
    try { host = new URL(u).host; } catch { /* not a URL */ }
    if (CDN_HOSTS.includes(host)) found.add(`cdn:${host}`);
  }
  return [...found].sort();
}
