import { SiteNav } from "@/components/SiteNav";
import { Hero } from "@/components/Hero";
import { SiteFooter } from "@/components/SiteFooter";
import { ProblemSection } from "@/components/sections/ProblemSection";
import { HowItWorksSection } from "@/components/sections/HowItWorksSection";
import { NetworkSection } from "@/components/sections/NetworkSection";
import { ForecastSection } from "@/components/sections/ForecastSection";
import { TrustSection } from "@/components/sections/TrustSection";
import { CtaSection } from "@/components/sections/CtaSection";

export default function Home() {
  return (
    <>
      <SiteNav />
      <main className="flex-1">
        <Hero />
        <ProblemSection />
        <HowItWorksSection />
        <NetworkSection />
        <ForecastSection />
        <TrustSection />
        <CtaSection />
      </main>
      <SiteFooter />
    </>
  );
}
