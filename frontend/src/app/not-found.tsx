import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 px-6 text-center">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="text-sm text-ink-500">
        That page does not exist in LUMEN.
      </p>
      <Link href="/dashboard" className="btn-primary">
        Go to dashboard
      </Link>
    </div>
  );
}
