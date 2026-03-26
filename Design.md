One piece of advice to add: Use pseudo-code. For example write your entire solution from end to end but use empty methods with default returns values and a lot of TODO comments. Maybe you know you need a method that takes in a list of strings and returns a specific one, so create one with a name that makes sense and have it return an empty string for now you can implement it later. Build up the full solution without actually making it work, then just build piece by piece. Much easier and faster to pivot with this approach.
------
One tip I would try is to write your documentation first before you write your code. Plan out what classes and methods you are going to need to get the job done and then write just the headers and docstrings. Then, when it comes time to code, all you’re doing is filling in the logic for a system that is already fully specified.
------
As boring as it may sound:

Make a high-level plan so you roughly know what to do first to last.

Make a diagram that tells how people should interact with your system, what is the system's business (as opposed to what it doesn't care about)

If your system is distributed, make a diagram that shows how components interact with each other.

------
The following books are usually recommended when this topic pops up:

"Think Like A Programmer" by V. Anton Spraul

"The Pragmatic Programmer" by Andrew Hunt and David Thomas

"Structure and Interpretation of Computer Programs" (SICP) by Ableton, Sussman, Sussman

"Code: The Hidden Language of Computer Hardware and Software" by Charles Petzold

Don't get distracted by the fact that these books deal with different programming languages that you might or might not know. The code is secondary in these books. The approach to the code is what counts.

------
And ideally get some feedback from some users, cause your ultimate goal is to provide a product or service, not to build an app.

You can have some high-level designs of how you think it might look, but don't spend too much time working out the details.

Spent a week writing lots of code just to get to that point and feel that the code should be valuable? Doesn't matter, that code is basically garbage. Well, you can probably re-use some of it if it's in its own little module, but your focus shouldn't be to try and write a bunch of re-usable code.

Now, after you have something working, you can start thinking about how to structure your code.

Now, you have an idea what all the different moving parts are. Maybe you discovered some new things while you were building that prototype and now you need to re-think your design.

The worst part about software engineering is having this intricate plan and design that you spent a lot if time and care on, only to find out it's no good.

------

Step 1 - Decompose into distinct capability domains 
1. authenticated web scraping - login to Floor system, navigate to specific bill, download testimony PDFs with the organization name which uploaded it
2. Transcript generation - Youtube video -> text transcript. Search by certain date on YouTube Senate Chamber channel, convert to transcript if available. 
3. Knowledge base construction - combine testimony docs + transcript + any additional documents into a queryable context 
4. AI analysis - Claude API for summarization, memo generation, stance detection 
5. Plugin Architecture - ability to bolt on GitHub repos (stance detection, etc)
6. User interface - accessible to non-technical legislative staff 

-----

Step 2 - Deployment Platform decision 

* Microsoft Coplit: free tier does not support plugin/extension API, requires Copilot Pro + admin approval 
* Standalone Web App: full control, will require hosting and a new URL () [Pinned Tab in Edge to reduce friction]

-----
FULL SYSTEM ARCHITECTURE 

(Browser) Hea