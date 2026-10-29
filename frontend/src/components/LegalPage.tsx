/**
 * Public legal and support pages.
 *
 * These pages are deliberately provider-neutral: they describe categories of
 * service providers, not vendor names. They are reachable without signing in
 * at `/?view=privacy`, `/?view=terms`, `/?view=refunds`, and `/?view=support`.
 */

import { useState } from "react";
import {
  ArrowLeft,
  Check,
  Copy,
  LifeBuoy,
  Mail,
  ShieldCheck,
} from "lucide-react";

import { useApp } from "@/context/AppContext";
import { useAuth } from "@/context/AuthContext";
import { LegalLinks } from "@/components/LegalLinks";
import { Logo } from "@/components/ui/Logo";
import type { LegalView } from "@/types";

const EFFECTIVE_DATE = "25 October 2026";
const DEFAULT_SUPPORT_EMAIL = "support@pitchpilot.app";

interface Section {
  heading: string;
  paragraphs?: string[];
  bullets?: string[];
}

const CONTENT: Record<
  LegalView,
  { title: string; intro: string; sections: Section[] }
> = {
  privacy: {
    title: "Privacy Policy",
    intro:
      "This policy explains what PitchPilot collects, why we collect it, how long we keep it, and the choices you have.",
    sections: [
      {
        heading: "What we collect",
        bullets: [
          "Account details: your email address, password hash, plan, and recording-consent timestamp.",
          "Content you create: scripts, outlines, keywords, knowledge cards, and coaching-session summaries.",
          "Live-session data: short transcript excerpts, coaching cues, and copilot messages. Audio is processed live and is not stored as a recording.",
          "Screen context: when you choose to share a tab or window, downscaled frames are analyzed transiently and then discarded. We do not keep the images.",
          "Billing records: plan changes, payment references, amounts, currency, and status. We do not receive or store your full card number.",
          "Technical data: limited request logs, error information, and security signals needed to run and protect the service.",
        ],
      },
      {
        heading: "How we use it",
        bullets: [
          "To provide live transcription, coaching cues, copilot suggestions, screen-aware answers, and post-session debriefs.",
          "To authenticate you, enforce plan limits, process payments, and prevent abuse.",
          "To provide support, investigate errors, and improve reliability.",
          "To meet legal, tax, and security obligations.",
        ],
      },
      {
        heading: "Legal bases",
        paragraphs: [
          "Depending on where you live, we rely on your consent (for recording and automated analysis of a call), performance of our contract with you (to provide the service), our legitimate interests (security, reliability, and product improvement), and legal obligations (billing and tax records).",
          "You can withdraw recording consent at any time by ending the session and contacting support. Withdrawal does not affect processing that already happened.",
        ],
      },
      {
        heading: "Retention",
        bullets: [
          "Transcript excerpts and coaching notes are removed after 30 days. Session summaries such as duration and cue counts remain so your history still works.",
          "Screen frames are discarded after analysis and are not retained.",
          "Audio is processed live and is not retained as a recording.",
          "Account, script, and knowledge-card data remain until you delete them or delete your account.",
          "Billing and payment records may be kept longer where tax or accounting law requires it.",
          "Support emails are kept only as long as needed to resolve the request and maintain a record of the outcome.",
        ],
      },
      {
        heading: "Service providers",
        paragraphs: [
          "We use service providers in these categories: cloud hosting and database, live transcription, automated language and vision analysis, payment processing, and support tooling. They process data on our instructions and under appropriate confidentiality and security terms.",
          "Some providers may process data outside your country. Where required, we use contractual safeguards for those transfers.",
        ],
      },
      {
        heading: "Security",
        paragraphs: [
          "We use encrypted connections, hashed passwords, short-lived connection credentials, access controls, and rate limiting. No service can be perfectly secure, so please use a unique password and tell us promptly if you suspect unauthorized access.",
        ],
      },
      {
        heading: "Your choices and rights",
        bullets: [
          "Download a copy of your data from Settings.",
          "Delete your account and associated content from Settings.",
          "Change your password from Settings; this signs out other devices.",
          "Ask us to correct inaccurate account information.",
          "Object to or restrict certain processing, or withdraw consent where we rely on it.",
          "Contact support to exercise a right that is not available in the app.",
        ],
      },
      {
        heading: "Cookies and browser storage",
        paragraphs: [
          "We use browser storage for your sign-in token and interface preferences. We do not use advertising trackers. Clearing browser storage signs you out and resets local preferences.",
        ],
      },
      {
        heading: "Children",
        paragraphs: [
          "PitchPilot is a professional tool for adults. It is not intended for anyone under 18, and we do not knowingly collect data from children.",
        ],
      },
      {
        heading: "Changes",
        paragraphs: [
          "We may update this policy as the service changes. We will post the new version here and update the effective date. If a change materially affects how we use your data, we will provide additional notice where appropriate.",
        ],
      },
    ],
  },
  terms: {
    title: "Terms of Service",
    intro:
      "These terms govern your use of PitchPilot. By creating an account or starting a session, you agree to them.",
    sections: [
      {
        heading: "The service",
        paragraphs: [
          "PitchPilot provides live speaking cues, conversation suggestions, screen-aware assistance, and post-session feedback during calls, pitches, and presentations.",
          "PitchPilot is a coaching aid, not a substitute for your own judgment. Automated suggestions can be incomplete or wrong. You are responsible for what you say, what you share, and the decisions you make.",
        ],
      },
      {
        heading: "Recording permission",
        paragraphs: [
          "You must have permission from everyone on the call before you capture, transcribe, or analyze their audio. Laws differ by location; it is your responsibility to obtain any required consent and to stop the session if you do not have it.",
          "Do not use PitchPilot to record or analyze people who have not agreed to it.",
        ],
      },
      {
        heading: "Accounts and security",
        bullets: [
          "Provide accurate account information and keep your password confidential.",
          "You are responsible for activity under your account.",
          "Tell us promptly if you believe your account has been compromised.",
          "We may require you to reset your password or verify account details to protect the service.",
        ],
      },
      {
        heading: "Acceptable use",
        bullets: [
          "Do not use PitchPilot for unlawful surveillance, harassment, fraud, or deception.",
          "Do not attempt to bypass plan limits, rate limits, consent gates, or security controls.",
          "Do not upload malware, probe the service, or interfere with other users.",
          "Do not resell, scrape, or copy the service except as allowed by law or with our written permission.",
          "Do not use the service to violate confidentiality obligations or intellectual-property rights.",
        ],
      },
      {
        heading: "Plans and billing",
        paragraphs: [
          "The free plan includes the limits shown in the app. PitchPilot Plus is NGN 22,500 per month and renews automatically until cancelled.",
          "Payments are processed by our payment provider. By subscribing, you authorize recurring charges to your selected payment method. Prices and plan features may change; we will show the new price before it applies.",
          "You can cancel from Settings. Access continues until the end of the paid period. Refunds are handled under our Refund Policy.",
        ],
      },
      {
        heading: "Availability and changes",
        paragraphs: [
          "We aim to keep PitchPilot available, but the service is provided on an as-is and as-available basis. Maintenance, provider outages, browser restrictions, and network problems can interrupt it.",
          "We may add, change, or remove features. If we discontinue a paid feature, we will provide a reasonable remedy where required.",
        ],
      },
      {
        heading: "Intellectual property",
        paragraphs: [
          "PitchPilot and its software, design, and brand are owned by us or our licensors. You retain ownership of the scripts, outlines, and other content you create. You grant us the limited rights needed to host, process, and display that content to provide the service.",
        ],
      },
      {
        heading: "Disclaimers and liability",
        paragraphs: [
          "To the maximum extent permitted by law, PitchPilot is provided without warranties of any kind. We are not liable for indirect, incidental, special, consequential, or punitive damages, or for lost profits, opportunities, or data.",
          "Where liability cannot be excluded, our total liability is limited to the amount you paid us for the service in the 3 months before the claim. Nothing in these terms limits rights that cannot be limited by law.",
        ],
      },
      {
        heading: "Suspension and termination",
        paragraphs: [
          "We may suspend or close an account that violates these terms, creates a security or abuse risk, or fails to pay. You can delete your account at any time from Settings. Provisions that by their nature should survive termination will survive.",
        ],
      },
      {
        heading: "Governing law",
        paragraphs: [
          "These terms are governed by the laws of the Federal Republic of Nigeria, without regard to conflict-of-law rules. Before starting a formal dispute, please contact support so we can try to resolve it.",
        ],
      },
    ],
  },
  refunds: {
    title: "Refund Policy",
    intro:
      "This policy explains how cancellations and refunds work for PitchPilot Plus.",
    sections: [
      {
        heading: "Cancel anytime",
        paragraphs: [
          "You can cancel PitchPilot Plus from Settings. Cancellation stops the next renewal. Your plan stays active until the end of the current paid period, and you keep Plus access until then.",
        ],
      },
      {
        heading: "Monthly subscription",
        paragraphs: [
          "Plus is a monthly subscription at NGN 22,500. Because access is granted immediately, partial months are generally not refundable. This does not affect any refund right you have under applicable law.",
        ],
      },
      {
        heading: "When we will refund",
        bullets: [
          "A duplicate charge for the same period.",
          "A charge you did not authorize, after we verify the issue.",
          "A technical error that prevented access for a material part of the paid period and that we could not fix.",
          "Any refund required by applicable consumer law.",
        ],
      },
      {
        heading: "How to request a refund",
        paragraphs: [
          "Contact support within 14 days of the charge. Include the email on your account, the payment reference if you have it, and a short description of the issue. We aim to respond within 2 business days.",
          "If you start a chargeback before contacting us, we may need to pause the account while the payment provider investigates.",
        ],
      },
      {
        heading: "Payment errors",
        paragraphs: [
          "If you were charged in error, contact us first. We will work with our payment provider to correct it. Do not send full card numbers, passwords, or one-time codes by email.",
        ],
      },
    ],
  },
  support: {
    title: "Support",
    intro:
      "Need help with your account, a session, or a payment? We are here to help.",
    sections: [
      {
        heading: "Contact us",
        paragraphs: [
          "Email us and include the address on your PitchPilot account. We aim to reply within 2 business days.",
        ],
      },
      {
        heading: "Account and data requests",
        bullets: [
          "Download your data or delete your account from Settings.",
          "To correct account information or ask a privacy question, email support.",
          "For deletion requests, email us from the address on the account so we can verify ownership.",
        ],
      },
      {
        heading: "Billing help",
        bullets: [
          "Include the payment reference from your plan history if available.",
          "Tell us whether you want to cancel, correct a charge, or understand a renewal.",
          "Never send full card numbers, passwords, or one-time codes.",
        ],
      },
      {
        heading: "Security reports",
        paragraphs: [
          "If you believe you found a security issue, email support with “Security” in the subject. Please give us a reasonable chance to investigate before disclosing it publicly.",
        ],
      },
      {
        heading: "Before you write",
        paragraphs: [
          "For a live-session issue, tell us which browser you used, whether you shared a tab/window or the whole screen, and what happened. Do not include full call transcripts or other people's personal data unless we ask for it.",
        ],
      },
    ],
  },
};

function SectionBlock({ section }: { section: Section }): JSX.Element {
  return (
    <section className="mt-7">
      <h2 className="text-sm font-bold text-white">{section.heading}</h2>
      {section.paragraphs?.map((paragraph) => (
        <p
          key={paragraph}
          className="mt-2 text-[13px] leading-relaxed text-slate-400"
        >
          {paragraph}
        </p>
      ))}
      {section.bullets !== undefined && (
        <ul className="mt-2.5 flex flex-col gap-2">
          {section.bullets.map((bullet) => (
            <li
              key={bullet}
              className="flex gap-2 text-[13px] leading-relaxed text-slate-400"
            >
              <span className="mt-[7px] h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400/70" />
              <span>{bullet}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SupportContact({ email }: { email: string }): JSX.Element {
  const [copied, setCopied] = useState(false);

  const copy = (): void => {
    const clipboard = navigator.clipboard;
    if (clipboard === undefined) return;
    void clipboard
      .writeText(email)
      .then(() => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      })
      .catch(() => {
        // Clipboard permission can be denied; the address is still selectable.
        setCopied(false);
      });
  };

  return (
    <div className="mt-5 rounded-2xl border border-amber-400/20 bg-amber-400/[0.06] p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="rounded-xl border border-amber-400/20 bg-amber-400/10 p-2 text-amber-300">
          <Mail size={16} />
        </span>
        <a
          href={`mailto:${email}`}
          className="text-sm font-semibold text-amber-200 underline decoration-amber-300/40 underline-offset-2 hover:text-amber-100"
        >
          {email}
        </a>
        <button
          type="button"
          onClick={copy}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-2.5 py-1.5 text-[11px] font-medium text-slate-400 transition-colors hover:border-white/20 hover:text-slate-200"
        >
          {copied ? <Check size={12} /> : <Copy size={12} />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <p className="mt-2 text-[11px] leading-relaxed text-slate-500">
        We never ask for your password, a one-time code, or full card details by
        email.
      </p>
    </div>
  );
}

export function LegalPage({ view }: { view: LegalView }): JSX.Element {
  const { setView } = useApp();
  const { user } = useAuth();
  const content = CONTENT[view];
  const supportEmail =
    import.meta.env.VITE_SUPPORT_EMAIL?.trim() || DEFAULT_SUPPORT_EMAIL;

  return (
    <main className="mx-auto w-full max-w-3xl px-4 pb-16 pt-6">
      <header className="mb-6 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={() => setView("dashboard")}
          className="btn-outline text-xs"
        >
          <ArrowLeft size={14} /> {user === null ? "Back to sign in" : "Back to app"}
        </button>
        <div className="flex items-center gap-2">
          <Logo size={30} />
          <span className="text-sm font-bold text-white">PitchPilot</span>
        </div>
        <LegalLinks className="ml-auto" />
      </header>

      <article className="panel p-6 sm:p-8">
        <div className="flex items-start gap-3">
          <span className="rounded-2xl border border-amber-400/20 bg-amber-400/10 p-2.5 text-amber-300">
            {view === "support" ? <LifeBuoy size={20} /> : <ShieldCheck size={20} />}
          </span>
          <div>
            <h1 className="text-2xl font-bold text-white">{content.title}</h1>
            <p className="mt-1 max-w-xl text-[13px] leading-relaxed text-slate-400">
              {content.intro}
            </p>
            <p className="mt-2 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
              Effective {EFFECTIVE_DATE}
            </p>
          </div>
        </div>

        {view === "support" && <SupportContact email={supportEmail} />}

        {content.sections.map((section) => (
          <SectionBlock key={section.heading} section={section} />
        ))}

        <div className="mt-8 border-t border-white/[0.06] pt-5">
          <p className="text-[11px] leading-relaxed text-slate-500">
            Questions about this page?{" "}
            <a
              href={`mailto:${supportEmail}`}
              className="font-semibold text-amber-300 underline decoration-amber-300/40 underline-offset-2 hover:text-amber-200"
            >
              Contact support
            </a>
            .
          </p>
          <LegalLinks className="mt-3" />
        </div>
      </article>
    </main>
  );
}
