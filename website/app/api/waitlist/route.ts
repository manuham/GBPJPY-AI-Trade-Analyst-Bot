import { NextRequest, NextResponse } from "next/server";
import { promises as fs } from "fs";
import path from "path";

const DATA_DIR = path.join(process.cwd(), "data");
const WAITLIST_FILE = path.join(DATA_DIR, "waitlist.json");

interface WaitlistEntry {
  email: string;
  joined_at: string;
  ip?: string;
}

async function getWaitlist(): Promise<WaitlistEntry[]> {
  try {
    const raw = await fs.readFile(WAITLIST_FILE, "utf-8");
    return JSON.parse(raw);
  } catch {
    return [];
  }
}

async function saveWaitlist(entries: WaitlistEntry[]) {
  await fs.mkdir(DATA_DIR, { recursive: true });
  await fs.writeFile(WAITLIST_FILE, JSON.stringify(entries, null, 2));
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const email = body.email?.trim().toLowerCase();

    // Validate email
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return NextResponse.json(
        { error: "Please enter a valid email address." },
        { status: 400 }
      );
    }

    const waitlist = await getWaitlist();

    // Check duplicate
    if (waitlist.some((e) => e.email === email)) {
      return NextResponse.json(
        { message: "You're already on the waitlist! We'll be in touch." },
        { status: 200 }
      );
    }

    // Add entry
    const entry: WaitlistEntry = {
      email,
      joined_at: new Date().toISOString(),
    };

    waitlist.push(entry);
    await saveWaitlist(waitlist);

    return NextResponse.json(
      {
        message: "Welcome aboard! You'll be the first to know when we launch.",
        position: waitlist.length,
      },
      { status: 201 }
    );
  } catch {
    return NextResponse.json(
      { error: "Server error — please try again later." },
      { status: 500 }
    );
  }
}

export async function GET() {
  const waitlist = await getWaitlist();
  return NextResponse.json({ count: waitlist.length });
}
