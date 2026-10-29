// Copy schema files into your EXISTING Sanity Studio and register these types.
// If no studio exists, use `npm create sanity@latest` and import these types.
import {defineConfig} from 'sanity'
import {structureTool} from 'sanity/structure'
import {ghlPost} from './schemas/ghlPost'
import {contentItem} from './schemas/contentItem'

export default defineConfig({
  name: 'default', title: 'GHL Content',
  projectId: process.env.SANITY_STUDIO_PROJECT_ID!,
  dataset: process.env.SANITY_STUDIO_DATASET || 'production',
  plugins: [structureTool()],
  schema: {types: [ghlPost, contentItem]},
})
