import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="py-16 text-center">
      <h1 className="text-2xl font-bold">Page not found</h1>
      <p className="mt-2 text-zinc-500">That page does not exist, or you may not have access to it.</p>
      <Link to="/" className="btn-primary mt-6">
        Back to the dashboard
      </Link>
    </div>
  );
}
