# GaaS (Greg as a Service) Manifesto

### The Principle of Reversibility

Every action that GaaS takes autonomously is reversible.

❌ Sending an email  
An email can be sent, it can not be unsent, this is a non-reversible action.

✅ Drafting an email  
A draft email can be created, it can be deleted, this is a reversible action.

❌ Googling an acronym found in an email  
A search query is sent to Google, private information has been sent to an untrusted system, this is a non-reversible action.

✅ Searching the user's notes for an acronym found in an email  
A grep command can scan a directory, no private information has left the system, this is a reversible action.

✅ Searching a local Wikipedia instance for an acronym found in an email  
`kiwix-search` is used to query a ZIM file, no private information has left the system, this is a reversible action.


### The Principle of Audibility

Every action the AI makes should be auditable.
Log what the agent does, don't ask it what it did.


### The Principle of Accountability

AI has ability but no accountability.
Every non reversible action requires human-in-the-loop.