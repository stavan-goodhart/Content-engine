import {defineField, defineType} from 'sanity'

export const ghlPost = defineType({
  name: 'ghlPost', title: 'GHL article', type: 'document',
  fields: [
    defineField({name: 'title', type: 'string', validation: r => r.required()}),
    defineField({name: 'slug', type: 'slug', options: {source: 'title'}, validation: r => r.required()}),
    defineField({name: 'category', type: 'string'}),
    defineField({name: 'excerpt', type: 'text'}),
    defineField({name: 'body', type: 'array', of: [{type: 'block'}]}),
    defineField({name: 'publishedAt', type: 'datetime'}),
    defineField({name: 'sources', type: 'array', of: [{type: 'object', fields: [
      {name: 'title', type: 'string'}, {name: 'url', type: 'url'},
    ]}]}),
    defineField({name: 'sourceNotes', type: 'text'}),
  ],
  preview: {select: {title: 'title', subtitle: 'category'}},
})
