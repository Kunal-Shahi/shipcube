import { Wordmark } from "@/components/logo";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex min-h-dvh flex-col px-4">
      <header className="mx-auto flex h-14 w-full max-w-[1120px] items-center">
        <Wordmark />
      </header>
      <main className="flex flex-1 items-start justify-center pt-[12vh] pb-16">{children}</main>
    </div>
  );
}
