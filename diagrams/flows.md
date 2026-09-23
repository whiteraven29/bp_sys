                START
                  │
                  ▼
          Student applies
                  │
                  ▼
          Admission created
                  │
                  ▼
       Application approved?
             /          \
           NO            YES
           │              │
           ▼              ▼
         Reject      Generate Student
                         Number
                          │
                          ▼
                  Assign Program
                          │
                          ▼
                  Assign Academic
                     Year/Intake
                          │
                          ▼
                 Generate Charges
                          │
                          ▼
                  Create Invoice
                          │
                          ▼
                  Student notified
                          │
                          ▼
                       PAYMENT
                          │
                          ▼
                  Payment verified
                          │
                          ▼
              Registration activated
                          │
                          ▼
                      END


                                   STUDENT
                 │
                 ▼
          Program Assigned
                 │
                 ▼
         Academic Year/Year
                 │
                 ▼
          Fee Structure
                 │
                 ▼
       Generate Student Charges
                 │
                 ▼
              INVOICE
                 │
        ┌────────┴────────┐
        │                 │
        ▼                 ▼
    Due Date          Payment Plan
        │                 │
        └────────┬────────┘
                 ▼
          Student Portal
                 │
                 ▼
        View Outstanding Fees




                       STUDENT
                    │
                    ▼
             View Invoice
                    │
                    ▼
             Select Payment
                    │
                    ▼
        ┌───────────┼────────────┐
        │           │            │
        ▼           ▼            ▼
      Bank       Mobile       Other
     Payment     Payment      Channel
        │           │            │
        └───────────┼────────────┘
                    ▼
             Payment Gateway
                    │
                    ▼
             Transaction ID
                    │
                    ▼
             Verify Payment
                    │
             ┌──────┴──────┐
             │             │
          SUCCESS        FAILED
             │             │
             ▼             ▼
        Record Payment   Log Failure
             │
             ▼
       Generate Receipt
             │
             ▼
       Update Student
       Financial Ledger
             │
             ▼
       Update Outstanding
           Balance
             │
             ▼
       Notify Student


         ## financial clearence
         Student
   │
   ▼
Completes studies
   │
   ▼
Academic clearance
   │
   ├── Library
   ├── Department
   ├── Examination
   └── Academic Office
   │
   ▼
Finance clearance
   │
   ▼
Check ledger
   │
   ▼
Outstanding balance?
      /       \
    YES        NO
     │          │
     ▼          ▼
Settlement    Finance
required      cleared
                 │
                 ▼
          Graduation/
          Certification


## expense workflow
Staff/User
    │
    ▼
Create Expense Request
    │
    ▼
Department Approval
    │
    ▼
Finance Review
    │
    ▼
Approved?
   /    \
 NO      YES
 │        │
 ▼        ▼
Reject   Payment
          │
          ▼
     Record Expense
          │
          ▼
     General Ledger
          │
          ▼
    Financial Report


### example on expenses
Income
 ├── Tuition
 ├── Registration
 ├── Examination
 ├── Accommodation
 └── Other fees

Expenses
 ├── Salaries
 ├── Utilities
 ├── Equipment
 ├── Stationery
 ├── Maintenance
 └── Other expenses

## sample class diagram
                           ┌───────────────────┐
                           │     Student       │
                           ├───────────────────┤
                           │ id                │
                           │ studentNo         │
                           │ name              │
                           │ phone             │
                           │ email             │
                           └─────────┬─────────┘
                                     │
                 ┌───────────────────┼───────────────────┐
                 │                   │                   │
                 ▼                   ▼                   ▼
       ┌─────────────────┐ ┌─────────────────┐ ┌──────────────────┐
       │   Enrollment    │ │ FinancialAccount│ │   Notification   │
       └────────┬────────┘ └────────┬────────┘ └──────────────────┘
                │                   │
                ▼                   ▼
       ┌─────────────────┐ ┌─────────────────┐
       │     Program     │ │LedgerTransaction│
       └────────┬────────┘ └────────┬────────┘
                │                   │
                ▼              ┌────┴───────┐
       ┌─────────────────┐     │            │
       │     Course      │     ▼            ▼
       └────────┬────────┘ ┌────────┐ ┌──────────┐
                │          │ Invoice│ │ Payment  │
                ▼          └────┬───┘ └────┬─────┘
       ┌─────────────────┐      │           │
       │ CourseRegistration│    │           ▼
       └─────────────────┘      │      ┌──────────┐
                                │      │ Receipt  │
                                │      └──────────┘
                                ▼
                         ┌──────────────┐
                         │ InvoiceItem  │
                         └──────┬───────┘
                                │
                                ▼
                         ┌──────────────┐
                         │   FeeType    │
                         └──────────────┘

## sample workflow
                         ┌─────────────┐
                         │   APPLICANT │
                         └──────┬──────┘
                                │
                                ▼
                         ┌─────────────┐
                         │  ADMISSION  │
                         └──────┬──────┘
                                │
                                ▼
                         ┌─────────────┐
                         │   STUDENT   │
                         │   CREATED   │
                         └──────┬──────┘
                                │
                  ┌─────────────┴─────────────┐
                  │                           │
                  ▼                           ▼
          ┌───────────────┐           ┌────────────────┐
          │   ACADEMICS   │           │    FINANCE     │
          └───────┬───────┘           └───────┬────────┘
                  │                           │
                  ▼                           ▼
             Enrollment                  Fee Structure
                  │                           │
                  ▼                           ▼
             Courses                      Invoice
                  │                           │
                  ▼                           ▼
          Course Registration             Payment
                  │                           │
                  ▼                           ▼
             Attendance                   Receipt
                  │                           │
                  ▼                           ▼
             Examination                  Ledger
                  │                           │
                  ▼                           │
               Results ◄─────────────────────┘
                  │
                  ▼
          Academic Clearance
                  │
                  ▼
          Financial Clearance
                  │
                  ▼
             Graduation

## db relationship sample
                    ACADEMIC_YEAR
                         │
             ┌───────────┴───────────┐
             ▼                       ▼
          PROGRAM                 SEMESTER
             │                       │
             ▼                       │
       FEE_STRUCTURE                 │
             │                       │
             └───────────┐           │
                         ▼           ▼
                       STUDENT ── ENROLLMENT
                          │           │
             ┌────────────┼───────────┤
             │            │           │
             ▼            ▼           ▼
          ACADEMIC    FINANCIAL    COURSE
           RECORD      ACCOUNT     REGISTRATION
                          │
                          ▼
                       INVOICE
                          │
                          ▼
                    INVOICE_ITEM
                          │
                          ▼
                       PAYMENT
                          │
                          ▼
                       RECEIPT
                          │
                          ▼
                    LEDGER ENTRY


>[!note]
>student.balance shoudnot be a manually editable database field



## audit trail
WHO
 │
 ▼
WHAT ACTION
 │
 ▼
ON WHICH RECORD
 │
 ▼
OLD VALUE
 │
 ▼
NEW VALUE
 │
 ▼
WHEN
 │
 ▼
FROM WHERE

>[!dont]
>dont delete financial transactionns use
Payment
   ↓
Reversal
   ↓
Adjustment

## some summary finace dashboard example
┌──────────────────────────────────────────────────┐
│                 COLLEGE DASHBOARD                │
├────────────┬────────────┬────────────┬───────────┤
│ Students   │ Active     │ Revenue    │ Expenses  │
│   1,250    │   1,080    │ 85.4M TZS  │ 42.1M    │
├────────────┴────────────┴────────────┴───────────┤
│                                                  │
│             PAYMENT COLLECTION                   │
│                                                  │
│        ███████████████████                       │
│                                                  │
├──────────────────────┬───────────────────────────┤
│ Academic             │ Financial                 │
│                      │                           │
│ 1080 Active          │ 25.4M Outstanding         │
│ 950 Registered       │ 85.4M Collected           │
│ 75 Pending Results   │ 42.1M Expenses            │
└──────────────────────┴───────────────────────────┘

## sample admission workflow
                         START
                           │
                           ▼
                 Select Admission Type
                           │
           ┌───────────────┼────────────────┐
           │               │                │
           ▼               ▼                ▼
       FIRST YEAR      CONTINUING       READMISSION
           │               │                │
           │               │                │
           ▼               ▼                ▼
       Application       Finance          Finance
       Information       Clearance        Clearance
           │               │                │
           ▼               ▼                ▼
         Finance          Records          Records
           │               │                │
           ▼               ▼                ▼
         Records         Admission        Admission
           │
           ▼
        Admission
           │
           ▼
         COMPLETE


## firts year admission
                    FIRST YEAR STUDENT
                           │
                           ▼
                    Admission Record
                           │
                           ▼
                  Capture Student Data
                           │
             ┌─────────────┼─────────────┐
             │             │             │
             ▼             ▼             ▼
          Personal     Education      Documents
          Details       History        Required
             │             │             │
             └─────────────┼─────────────┘
                           ▼
                     Finance Officer
                           │
                           ▼
                    Create Financial
                       Obligations
                           │
                           ▼
                    Payment/Clearance
                           │
                           ▼
                     Records Officer
                           │
                           ▼
                    Verify Documents
                           │
                           ▼
                    Create/Activate
                    Student Record
                           │
                           ▼
                   Admission Officer
                           │
                           ▼
                    Final Admission
                           │
                           ▼
                        COMPLETE

## Admission requirement module
AdmissionRequirement
--------------------
id
name
description
requirement_type
mandatory
applies_to
academic_year
active

## requirement verification 
Student
   │
   ▼
Admission Requirements
   │
   ├── TPH Book
   │     └── ✓ Verified
   │
   ├── Insurance
   │     └── ✓ Verified
   │
   ├── Calculator
   │     └── ✓ Verified
   │
   └── Rim Paper
         └── ✗ Missing

>[!note]
>if a student does not have TPH as a book then must pay so finance write payment debt, same for insurance if they dont have for themselves then must pay college to buy them one, these are mandatory at a first semester of academic year and reverified on second semester of thatr same academic year
>

## continuing student workflow
                CONTINUING STUDENT
                        │
                        ▼
                   FINANCE
                        │
                 Check account
                        │
             ┌──────────┴──────────┐
             │                     │
          Cleared              Not Cleared
             │                     │
             ▼                     ▼
          RECORDS              Payment
             │
             ▼
       Check academic status
             │
             ▼
         Eligible?
          /     \
        YES      NO
         │        │
         ▼        ▼
     ADMISSION   Resolve
         │       academic
         ▼       issue
     COMPLETE

## continuing student with repeat modules 
                 CONTINUING STUDENT
                        │
                        ▼
                      FINANCE
                        │
                        ▼
                     RECORDS
                        │
                        ▼
                  Retrieve Results
                        │
                        ▼
                  Analyze Results
                        │
             ┌──────────┼───────────┐
             │          │           │
             ▼          ▼           ▼
          PASS ALL    1–2 FAIL    SEMESTER FAIL
             │          │           │
             ▼          ▼           ▼
         CONTINUE    REPEATER    READMISSION
                        │           │
                        ▼           ▼
                    Calculate    Calculate
                    repeat fee   readmission
                        │           │
                        └─────┬─────┘
                              ▼
                         ADMISSION

## readmission
Student
   ↓
Previous semester = Semester 1
   ↓
Status = READMISSION
   ↓
Records confirms
   ↓
Finance generates applicable charges
   ↓
Payment
   ↓
Records
   ↓
Admission

## final sample workflow
                           STUDENT
                              │
                              ▼
                     ADMISSION REQUEST
                              │
                              ▼
                    ┌──────────────────┐
                    │ Admission Type?  │
                    └────────┬─────────┘
                             │
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
      FIRST YEAR        CONTINUING          READMISSION
          │                  │                  │
          │                  │                  │
          ▼                  ▼                  ▼
      Create/Collect       FINANCE           FINANCE
       information           │                  │
          │                  ▼                  ▼
          ▼               Financial          Financial
       FINANCE             Check              Check
          │                  │                  │
          ▼                  ▼                  ▼
       Payment            RECORDS            RECORDS
          │                  │                  │
          ▼                  ▼                  ▼
       RECORDS          Check Results       Verify Semester
          │                  │                  │
          ▼                  │                  │
       Verify               │                  │
       Documents            │                  │
          │                  ▼                  ▼
          │             Determine:          Confirm:
          │             Normal / Repeat     Readmission
          │                  │                  │
          └──────────────────┼──────────────────┘
                             ▼
                      ADMISSION OFFICER
                             │
                             ▼
                    Finalize Admission
                             │
                             ▼
                         COMPLETE

## avoid mix admission, registration and enrollment
                   ┌─────────────────────┐
                   │ ADMISSION REQUEST   │
                   └──────────┬──────────┘
                              │
                              ▼
                    ┌──────────────────┐
                    │ ADMISSION TYPE   │
                    └────────┬─────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
    FIRST YEAR          CONTINUING          READMISSION
        │                    │                    │
        └───────────┐        │        ┌───────────┘
                    ▼        ▼        ▼
                    ┌──────────────────┐
                    │ WORKFLOW ENGINE  │
                    └────────┬─────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
           FINANCE        RECORDS        ADMISSION
              │              │              │
              ▼              ▼              ▼
          Clearance      Verification    Finalization
              │              │              │
              └──────────────┼──────────────┘
                             ▼
                    STUDENT REGISTRATION
                             │
                 ┌───────────┴───────────┐
                 ▼                       ▼
          Normal Courses          Repeat Courses

## sample core classes
┌──────────────────────┐
│       Student        │
├──────────────────────┤
│ id                   │
│ student_number       │
│ name                 │
│ phone                │
└──────────┬───────────┘
           │
           ├───────────────────────┐
           │                       │
           ▼                       ▼
┌──────────────────┐      ┌─────────────────────┐
│    Admission     │      │ FinancialAccount    │
├──────────────────┤      └──────────┬──────────┘
│ admission_type   │                 │
│ academic_year    │                 ▼
│ status           │          ┌──────────────┐
└────────┬─────────┘          │ Transactions │
         │                    └──────────────┘
         ▼
┌──────────────────────┐
│   Enrollment         │
├──────────────────────┤
│ program              │
│ academic_year        │
│ status               │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ SemesterRegistration│
├──────────────────────┤
│ semester             │
│ status               │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│ CourseRegistration   │
├──────────────────────┤
│ course               │
│ registration_type    │
│ NORMAL / REPEAT      │
└──────────────────────┘

>[!note]
>the college also expects to add new courses with different departments so make sure in your design currently this ias athered
>


