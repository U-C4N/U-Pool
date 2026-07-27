/**
 * The UI ships as a static export: `next build` writes plain HTML/JS to ui/out,
 * which the Python shell serves over loopback. No Node runtime is involved once
 * the app is packaged.
 */
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "export",
  reactStrictMode: true,
  images: { unoptimized: true },
};

export default nextConfig;
