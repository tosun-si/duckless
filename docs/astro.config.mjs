// @ts-check
import { defineConfig } from 'astro/config';
import starlight from '@astrojs/starlight';

// Served by GitHub Pages at https://tosun-si.github.io/duckless/
export default defineConfig({
	site: 'https://tosun-si.github.io',
	base: '/duckless',
	integrations: [
		starlight({
			title: 'DuckLess',
			description:
				'Serverless DuckDB on Google Cloud: run SQL or your own code on a right-sized VM in your project, then tear it down.',
			social: [{ icon: 'github', label: 'GitHub', href: 'https://github.com/tosun-si/duckless' }],
			editLink: { baseUrl: 'https://github.com/tosun-si/duckless/edit/main/docs/' },
			lastUpdated: true,
			sidebar: [
				{
					label: 'Start here',
					items: [
						{ label: 'Why DuckLess', slug: 'start/why' },
						{ label: 'Quick start', slug: 'start/quickstart' },
					],
				},
				{
					label: 'Guides',
					items: [
						{ label: 'Writing jobs', slug: 'guides/writing-jobs' },
						{ label: 'Machines, Spot and spill', slug: 'guides/machines' },
						{ label: 'Infrastructure', slug: 'guides/infrastructure' },
					],
				},
				{
					label: 'Reference',
					items: [
						{ label: 'CLI', slug: 'reference/cli' },
						{ label: 'Configuration', slug: 'reference/configuration' },
						{ label: 'Architecture', slug: 'reference/architecture' },
					],
				},
				{ label: 'Benchmarks', slug: 'benchmarks' },
			],
		}),
	],
});
