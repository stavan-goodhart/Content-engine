import {defineField, defineType} from 'sanity'

export const contentItem = defineType({
  name: 'contentItem', title: 'Content pipeline', type: 'document',
  fields: [
    defineField({name: 'title', type: 'string'}),
    defineField({name: 'notes', type: 'text'}),
    defineField({name: 'url', type: 'url'}),
    defineField({name: 'publisher', type: 'string'}),
    defineField({name: 'status', type: 'string', options: {list: [
      'topic_review','researching','draft_review','changes_requested','publishing','published','rejected','error',
    ]}}),
    defineField({name: 'articleTitle', type: 'string'}),
    defineField({name: 'slug', type: 'string'}),
    defineField({name: 'excerpt', type: 'text'}),
    defineField({name: 'body', type: 'text', rows: 25}),
    defineField({name: 'linkedinText', type: 'text'}),
    defineField({name: 'xText', type: 'text'}),
    defineField({name: 'substackBody', type: 'text', rows: 25}),
    defineField({name: 'sources', type: 'array', of: [{type: 'object', fields: [
      {name: 'title', type: 'string'}, {name: 'url', type: 'url'}, {name: 'access', type: 'string'},
    ]}]}),
    defineField({name: 'slackThreadTs', type: 'string'}),
    defineField({name: 'approvedAt', type: 'datetime'}),
    defineField({name: 'distributions', type: 'text'}),
  ],
  preview: {select: {title: 'title', subtitle: 'status'}},
})
