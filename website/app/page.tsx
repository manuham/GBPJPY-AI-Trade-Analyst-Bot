import Hero from "./components/Hero";
import LiveStats from "./components/LiveStats";
import HowItWorks from "./components/HowItWorks";
import TradeTable from "./components/TradeTable";
import Pricing from "./components/Pricing";
import Footer from "./components/Footer";

export default function Home() {
  return (
    <main>
      <Hero />
      <LiveStats />
      <HowItWorks />
      <TradeTable />
      <Pricing />
      <Footer />
    </main>
  );
}
