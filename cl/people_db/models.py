import re

from django.urls import reverse
from django.db import models
from django.template import loader
from django.utils.text import slugify
from localflavor.us import models as local_models

from cl.custom_filters.templatetags.extras import granular_date
from cl.lib.date_time import midnight_pst
from cl.lib.model_helpers import (
    make_choices_group_lookup,
    validate_has_full_name,
    validate_is_not_alias,
    validate_partial_date,
    validate_nomination_fields_ok,
    validate_all_or_none,
    validate_exactly_n,
    validate_not_all,
    validate_at_most_n,
    validate_supervisor,
    make_pdf_path,
)
from cl.lib.search_index_utils import (
    solr_list,
    null_map,
    normalize_search_dicts,
)
from cl.lib.storage import IncrementingFileSystemStorage, AWSMediaStorage
from cl.lib.models import THUMBNAIL_STATUSES
from cl.lib.string_utils import trunc
from cl.search.models import Court

SUFFIXES = (
    ("jr", "Jr."),
    ("sr", "Sr."),
    ("1", "I"),
    ("2", "II"),
    ("3", "III"),
    ("4", "IV"),
)
GENDERS = (
    ("m", "Male"),
    ("f", "Female"),
    ("o", "Other"),
)
GRANULARITY_YEAR = "%Y"
GRANULARITY_MONTH = "%Y-%m"
GRANULARITY_DAY = "%Y-%m-%d"
DATE_GRANULARITIES = (
    (GRANULARITY_YEAR, "Year"),
    (GRANULARITY_MONTH, "Month"),
    (GRANULARITY_DAY, "Day"),
)


class Person(models.Model):
    RELIGIONS = (
        ("ca", "Catholic"),
        ("pr", "Protestant"),
        ("je", "Jewish"),
        ("mu", "Muslim"),
        ("at", "Atheist"),
        ("ag", "Agnostic"),
        ("mo", "Mormon"),
        ("bu", "Buddhist"),
        ("hi", "Hindu"),
    )
    race = models.ManyToManyField(
        "Race",
        help_text="A person's race or races if they are multi-racial.",
        blank=True,
    )
    is_alias_of = models.ForeignKey(
        "self",
        help_text="Any nicknames or other aliases that a person has. For "
        "example, William Jefferson Clinton has an alias to Bill",
        related_name="aliases",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    date_completed = models.DateTimeField(
        help_text="Whenever an editor last decided that a profile was "
        "complete in some sense.",
        blank=True,
        null=True,
    )
    fjc_id = models.IntegerField(
        help_text="The ID of a judge as assigned by the Federal Judicial "
        "Center.",
        null=True,
        blank=True,
        unique=True,
        db_index=True,
    )
    cl_id = models.CharField(
        max_length=30,
        help_text="A unique identifier for judge, also indicating source of "
        "data.",
        unique=True,
        db_index=True,
    )
    slug = models.SlugField(
        help_text="A generated path for this item as used in CourtListener "
        "URLs",
        max_length=158,  # len(self.name_full)
    )
    name_first = models.CharField(
        help_text="The first name of this person.",
        max_length=50,
    )
    name_middle = models.CharField(
        help_text="The middle name or names of this person",
        max_length=50,
        blank=True,
    )
    name_last = models.CharField(
        help_text="The last name of this person",
        max_length=50,
        db_index=True,
    )
    name_suffix = models.CharField(
        help_text="Any suffixes that this person's name may have",
        choices=SUFFIXES,
        max_length=5,
        blank=True,
    )
    date_dob = models.DateField(
        help_text="The date of birth for the person",
        null=True,
        blank=True,
    )
    date_granularity_dob = models.CharField(
        choices=DATE_GRANULARITIES,
        max_length=15,
        blank=True,
    )
    date_dod = models.DateField(
        help_text="The date of death for the person",
        null=True,
        blank=True,
    )
    date_granularity_dod = models.CharField(
        choices=DATE_GRANULARITIES,
        max_length=15,
        blank=True,
    )
    dob_city = models.CharField(
        help_text="The city where the person was born.",
        max_length=50,
        blank=True,
    )
    dob_state = local_models.USStateField(
        help_text="The state where the person was born.",
        blank=True,
    )
    dob_country = models.CharField(
        help_text="The country where the person was born.",
        blank=True,
        default="United States",
        max_length=50,
    )
    dod_city = models.CharField(
        help_text="The city where the person died.",
        max_length=50,
        blank=True,
    )
    dod_state = local_models.USStateField(
        help_text="The state where the person died.",
        blank=True,
    )
    dod_country = models.CharField(
        help_text="The country where the person died.",
        blank=True,
        default="United States",
        max_length=50,
    )
    gender = models.CharField(
        help_text="The person's gender",
        choices=GENDERS,
        max_length=2,
        blank=True,
    )
    religion = models.CharField(
        help_text="The religion of a person", max_length=30, blank=True
    )
    ftm_total_received = models.FloatField(
        help_text="The amount of money received by this person and logged by "
        "Follow the Money.",
        blank=True,
        null=True,
        db_index=True,
    )
    ftm_eid = models.CharField(
        max_length=30,
        help_text="The ID of a judge as assigned by the Follow the Money "
        "database.",
        null=True,
        blank=True,
    )
    has_photo = models.BooleanField(
        help_text="Whether there is a photo corresponding to this person in "
        "the judge pics project.",
        default=False,
    )

    def __str__(self):
        return "%s: %s" % (self.pk, self.name_full)

    class Meta:
        verbose_name_plural = "people"

    def get_absolute_url(self):
        return reverse("view_person", args=[self.pk, self.slug])

    def save(self, *args, **kwargs):
        self.slug = slugify(trunc(self.name_full, 158))
        self.full_clean()
        super(Person, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        validate_partial_date(self, ["dob", "dod"])
        validate_is_not_alias(self, ["is_alias_of"])
        validate_has_full_name(self)
        super(Person, self).clean_fields(*args, **kwargs)

    @property
    def name_full(self):
        return " ".join(
            [
                v
                for v in [
                    self.name_first,
                    self.name_middle,
                    self.name_last,
                    self.get_name_suffix_display(),
                ]
                if v
            ]
        ).strip()

    @property
    def name_full_reverse(self):
        return "{name_last}, {name_first} {name_middle}, {suffix}".format(
            suffix=self.get_name_suffix_display(), **self.__dict__
        ).strip(", ")

    @property
    def is_alias(self):
        return True if self.is_alias_of is not None else False

    @property
    def is_judge(self):
        """Examine the positions a person has had and identify if they were ever
        a judge.
        """
        for position in self.positions.all():
            if position.is_judicial_position:
                return True
        return False

    def as_search_dict(self):
        """Create a dict that can be ingested by Solr"""
        out = {
            "id": self.pk,
            "fjc_id": self.fjc_id,
            "cl_id": self.cl_id,
            "alias_ids": [alias.pk for alias in self.aliases.all()],
            "races": [r.get_race_display() for r in self.race.all()],
            "gender": self.get_gender_display(),
            "religion": self.religion,
            "name": self.name_full,
            "name_reverse": self.name_full_reverse,
            "date_granularity_dob": self.date_granularity_dob,
            "date_granularity_dod": self.date_granularity_dod,
            "dob_city": self.dob_city,
            "dob_state": self.get_dob_state_display(),
            "dob_state_id": self.dob_state,
            "absolute_url": self.get_absolute_url(),
            "school": [e.school.name for e in self.educations.all()],
            "political_affiliation": [
                pa.get_political_party_display()
                for pa in self.political_affiliations.all()
                if pa
            ],
            "political_affiliation_id": [
                pa.political_party
                for pa in self.political_affiliations.all()
                if pa
            ],
            "aba_rating": [
                r.get_rating_display() for r in self.aba_ratings.all() if r
            ],
        }

        # Dates
        if self.date_dob is not None:
            out["dob"] = midnight_pst(self.date_dob)
        if self.date_dod is not None:
            out["dod"] = midnight_pst(self.date_dod)

        # Joined Values. Brace yourself.
        positions = self.positions.all()
        if positions.count() > 0:
            p_out = {
                "court": [p.court.short_name for p in positions if p.court],
                "court_exact": [p.court.pk for p in positions if p.court],
                "position_type": [
                    p.get_position_type_display() for p in positions
                ],
                "appointer": [
                    p.appointer.person.name_full_reverse
                    for p in positions
                    if p.appointer
                ],
                "supervisor": [
                    p.supervisor.name_full_reverse
                    for p in positions
                    if p.supervisor
                ],
                "predecessor": [
                    p.predecessor.name_full_reverse
                    for p in positions
                    if p.predecessor
                ],
                "date_nominated": solr_list(positions, "date_nominated"),
                "date_elected": solr_list(positions, "date_elected"),
                "date_recess_appointment": solr_list(
                    positions,
                    "date_recess_appointment",
                ),
                "date_referred_to_judicial_committee": solr_list(
                    positions,
                    "date_referred_to_judicial_committee",
                ),
                "date_judicial_committee_action": solr_list(
                    positions,
                    "date_judicial_committee_action",
                ),
                "date_hearing": solr_list(positions, "date_hearing"),
                "date_confirmation": solr_list(positions, "date_confirmation"),
                "date_start": solr_list(positions, "date_start"),
                "date_granularity_start": solr_list(
                    positions,
                    "date_granularity_start",
                ),
                "date_retirement": solr_list(
                    positions,
                    "date_retirement",
                ),
                "date_termination": solr_list(
                    positions,
                    "date_termination",
                ),
                "date_granularity_termination": solr_list(
                    positions,
                    "date_granularity_termination",
                ),
                "judicial_committee_action": [
                    p.get_judicial_committee_action_display()
                    for p in positions
                    if p.judicial_committee_action
                ],
                "nomination_process": [
                    p.get_nomination_process_display()
                    for p in positions
                    if p.nomination_process
                ],
                "selection_method": [
                    p.get_how_selected_display()
                    for p in positions
                    if p.how_selected
                ],
                "selection_method_id": [
                    p.how_selected for p in positions if p.how_selected
                ],
                "termination_reason": [
                    p.get_termination_reason_display()
                    for p in positions
                    if p.termination_reason
                ],
            }
            out.update(p_out)

        text_template = loader.get_template("indexes/person_text.txt")
        out["text"] = text_template.render({"item": self}).translate(null_map)

        return normalize_search_dicts(out)


class School(models.Model):
    is_alias_of = models.ForeignKey(
        "self",
        help_text="Any alternate names that a school may have",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified",
        auto_now=True,
        db_index=True,
    )
    name = models.CharField(
        help_text="The name of the school or alias",
        max_length=120,  # Dept. Ed. bulk data had a max of 91.
        db_index=True,
    )
    ein = models.IntegerField(
        help_text="The EIN assigned by the IRS",
        null=True,
        blank=True,
        db_index=True,
    )

    def __str__(self):
        if self.is_alias_of:
            return "%s: %s (alias: %s)" % (
                self.pk,
                self.name,
                self.is_alias_of.name,
            )
        else:
            return "%s: %s" % (self.pk, self.name)

    @property
    def is_alias(self):
        return True if self.is_alias_of is not None else False

    def save(self, *args, **kwargs):
        self.full_clean()
        super(School, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        # An alias cannot be an alias.
        validate_is_not_alias(self, ["is_alias_of"])
        super(School, self).clean_fields(*args, **kwargs)


class Position(models.Model):
    """A role held by a person, and the details about it."""

    JUDGE = "jud"
    # Acting
    ACTING_JUDGE = "act-jud"
    ACTING_PRESIDING_JUDGE = "act-pres-jud"
    # Associate
    ASSOCIATE_JUDGE = "ass-jud"
    ASSOCIATE_CHIEF_JUDGE = "ass-c-jud"
    ASSOCIATE_PRESIDING_JUDGE = "ass-pres-jud"
    JUSTICE = "jus"
    # Chief
    CHIEF_JUDGE = "c-jud"
    CHIEF_JUSTICE = "c-jus"
    CHIEF_SPECIAL_MASTER = "c-spec-m"
    PRESIDING_JUDGE = "pres-jud"
    PRESIDING_JUSTICE = "pres-jus"
    # Commissioner
    COMMISSIONER = "com"
    DEPUTY_COMMISSIONER = "com-dep"
    # Pro Tem
    JUDGE_PRO_TEM = "jud-pt"
    JUSTICE_PRO_TEM = "jus-pt"
    # Referee
    JUDGE_TRIAL_REFEREE = "ref-jud-tr"
    OFFICIAL_REFEREE = "ref-off"
    STATE_TRIAL_REFEREE = "ref-state-trial"
    # Retired
    ACTIVE_RETIRED_JUSTICE = "ret-act-jus"
    RETIRED_ASSOCIATE_JUDGE = "ret-ass-jud"
    RETIRED_CHIEF_JUDGE = "ret-c-jud"
    RETIRED_JUSTICE = "ret-jus"
    SENIOR_JUDGE = "ret-senior-jud"
    # Magistrate
    MAGISTRATE = "mag"
    CHIEF_MAGISTRATE = "c-mag"
    PRESIDING_MAGISTRATE = "pres-mag"
    MAGISTRATE_PRO_TEM = "mag-pt"
    MAGISTRATE_RECALLED = "mag-rc"
    MAGISTRATE_PART_TIME = "mag-part-time"
    # Special
    SPECIAL_CHAIRMAN = "spec-chair"
    SPECIAL_JUDGE = "spec-jud"
    SPECIAL_MASTER = "spec-m"
    SPECIAL_SUPERIOR_COURT_JUDGE_FOR_COMPLEX_BUSINESS_CASES = "spec-scjcbc"
    # Other
    CHAIRMAN = "chair"
    CHANCELLOR = "chan"
    PRESIDENT = "presi-jud"
    RESERVE_JUDGE = "res-jud"
    TRIAL_JUDGE = "trial-jud"
    VICE_CHANCELLOR = "vice-chan"
    VICE_CHIEF_JUDGE = "vice-cj"
    # Attorney General
    ATTORNEY_GENERAL = "att-gen"
    ASSISTANT_ATTORNEY_GENERAL = "att-gen-ass"
    SPECIAL_ASSISTANT_ATTORNEY_GENERAL = "att-gen-ass-spec"
    SENIOR_COUNSEL = "sen-counsel"
    DEPUTY_SOLICITOR_GENERAL = "dep-sol-gen"
    # More roles
    USA_PRESIDENT = "pres"
    GOVERNOR = "gov"
    CLERK = "clerk"
    CLERK_CHIEF_DEPUTY = "clerk-chief-dep"
    STAFF_ATTORNEY = "staff-atty"
    PROFESSOR = "prof"
    PRACTITIONER = "prac"
    PROSECUTOR = "pros"
    PUBLIC_DEFENDER = "pub-def"
    LEGISLATOR = "legis"

    POSITION_TYPES = (
        (
            "Judge",
            (
                # Acting
                (ACTING_JUDGE, "Acting Judge"),
                (
                    ACTING_PRESIDING_JUDGE,
                    "Acting Presiding Judge",
                ),
                # Associate
                (ASSOCIATE_JUDGE, "Associate Judge"),
                (ASSOCIATE_CHIEF_JUDGE, "Associate Chief Judge"),
                (ASSOCIATE_PRESIDING_JUDGE, "Associate Presiding Judge"),
                (JUDGE, "Judge"),
                (JUSTICE, "Justice"),
                # Chief
                (CHIEF_JUDGE, "Chief Judge"),
                (CHIEF_JUSTICE, "Chief Justice"),
                (CHIEF_SPECIAL_MASTER, "Chief Special Master"),
                (PRESIDING_JUDGE, "Presiding Judge"),
                (PRESIDING_JUSTICE, "Presiding Justice"),
                # Commissioner
                (COMMISSIONER, "Commissioner"),
                (DEPUTY_COMMISSIONER, "Deputy Commissioner"),
                # Pro Tem
                (JUDGE_PRO_TEM, "Judge Pro Tem"),
                (JUSTICE_PRO_TEM, "Justice Pro Tem"),
                # Referee
                (JUDGE_TRIAL_REFEREE, "Judge Trial Referee"),
                (OFFICIAL_REFEREE, "Official Referee"),
                (STATE_TRIAL_REFEREE, "State Trial Referee"),
                # Retired
                (ACTIVE_RETIRED_JUSTICE, "Active Retired Justice"),
                (RETIRED_ASSOCIATE_JUDGE, "Retired Associate Judge"),
                (RETIRED_CHIEF_JUDGE, "Retired Chief Judge"),
                (RETIRED_JUSTICE, "Retired Justice"),
                (SENIOR_JUDGE, "Senior Judge"),
                # Magistrate
                (MAGISTRATE, "Magistrate"),
                (CHIEF_MAGISTRATE, "Chief Magistrate"),
                (PRESIDING_MAGISTRATE, "Presiding Magistrate"),
                (MAGISTRATE_PRO_TEM, "Magistrate Pro Tem"),
                (MAGISTRATE_RECALLED, "Magistrate (Recalled)"),
                (MAGISTRATE_PART_TIME, "Magistrate (Part-Time)"),
                # Special
                (SPECIAL_CHAIRMAN, "Special Chairman"),
                (SPECIAL_JUDGE, "Special Judge"),
                (SPECIAL_MASTER, "Special Master"),
                (
                    SPECIAL_SUPERIOR_COURT_JUDGE_FOR_COMPLEX_BUSINESS_CASES,
                    "Special Superior Court Judge for Complex Business "
                    "Cases",
                ),
                # Other
                (CHAIRMAN, "Chairman"),
                (CHANCELLOR, "Chancellor"),
                (PRESIDENT, "President"),
                (RESERVE_JUDGE, "Reserve Judge"),
                (TRIAL_JUDGE, "Trial Judge"),
                (VICE_CHANCELLOR, "Vice Chancellor"),
                (VICE_CHIEF_JUDGE, "Vice Chief Judge"),
            ),
        ),
        # Sometimes attorney generals write opinions too
        (
            "Attorney General",
            (
                (ATTORNEY_GENERAL, "Attorney General"),
                (ASSISTANT_ATTORNEY_GENERAL, "Assistant Attorney General"),
                (
                    SPECIAL_ASSISTANT_ATTORNEY_GENERAL,
                    "Special Assistant Attorney General",
                ),
                (SENIOR_COUNSEL, "Senior Counsel"),
                (DEPUTY_SOLICITOR_GENERAL, "Deputy Solicitor General"),
            ),
        ),
        (
            "Appointing Authority",
            (
                (USA_PRESIDENT, "President of the United States"),
                (GOVERNOR, "Governor"),
            ),
        ),
        (
            "Clerkships",
            (
                (CLERK, "Clerk"),
                (CLERK_CHIEF_DEPUTY, "Chief Deputy Clerk"),
                (STAFF_ATTORNEY, "Staff Attorney"),
            ),
        ),
        (PROFESSOR, "Professor"),
        (PRACTITIONER, "Practitioner"),
        (PROSECUTOR, "Prosecutor"),
        (PUBLIC_DEFENDER, "Public Defender"),
        (LEGISLATOR, "Legislator"),
    )
    POSITION_TYPE_GROUPS = make_choices_group_lookup(POSITION_TYPES)
    PRIVATE = 1
    PUBLIC = 2
    SECTORS = (
        (PRIVATE, "Private sector"),
        (PUBLIC, "Public sector"),
    )
    NOMINATION_PROCESSES = (
        ("fed_senate", "U.S. Senate"),
        ("state_senate", "State Senate"),
        ("election", "Primary Election"),
        ("merit_comm", "Merit Commission"),
    )
    VOTE_TYPES = (
        ("s", "Senate"),
        ("p", "Partisan Election"),
        ("np", "Non-Partisan Election"),
    )
    JUDICIAL_COMMITTEE_ACTIONS = (
        ("no_rep", "Not Reported"),
        ("rep_w_rec", "Reported with Recommendation"),
        ("rep_wo_rec", "Reported without Recommendation"),
        ("rec_postpone", "Recommendation Postponed"),
        ("rec_bad", "Recommended Unfavorably"),
    )
    SELECTION_METHODS = (
        (
            "Election",
            (
                ("e_part", "Partisan Election"),
                ("e_non_part", "Non-Partisan Election"),
            ),
        ),
        (
            "Appointment",
            (
                ("a_pres", "Appointment (President)"),
                ("a_gov", "Appointment (Governor)"),
                ("a_legis", "Appointment (Legislature)"),
                # FISC appointments are made by the chief justice of SCOTUS
                ("a_judge", "Appointment (Judge)"),
            ),
        ),
    )
    SELECTION_METHOD_GROUPS = make_choices_group_lookup(SELECTION_METHODS)
    TERMINATION_REASONS = (
        ("ded", "Death"),
        ("retire_vol", "Voluntary Retirement"),
        ("retire_mand", "Mandatory Retirement"),
        ("resign", "Resigned"),
        ("other_pos", "Appointed to Other Judgeship"),
        ("lost", "Lost Election"),
        ("abolished", "Court Abolished"),
        ("bad_judge", "Impeached and Convicted"),
        ("recess_not_confirmed", "Recess Appointment Not Confirmed"),
        ("termed_out", "Term Limit Reached"),
    )
    position_type = models.CharField(
        help_text="If this is a judicial position, this indicates the role "
        "the person had. This field may be blank if job_title is "
        "complete instead.",
        choices=POSITION_TYPES,
        max_length=20,
        blank=True,
        null=True,
    )
    job_title = models.CharField(
        help_text="If title isn't in position_type, a free-text position may "
        "be entered here.",
        max_length=100,
        blank=True,
    )
    sector = models.SmallIntegerField(
        help_text="Whether the job was private or public sector.",
        choices=SECTORS,
        default=None,
        blank=True,
        null=True,
    )
    person = models.ForeignKey(
        Person,
        help_text="The person that held the position.",
        related_name="positions",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    court = models.ForeignKey(
        Court,
        help_text="If this was a judicial position, this is the jurisdiction "
        "where it was held.",
        related_name="court_positions",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    school = models.ForeignKey(
        School,
        help_text="If this was an academic job, this is the school where the "
        "person worked.",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    organization_name = models.CharField(
        help_text="If the organization where this position was held is not a "
        "school or court, this is the place it was held.",
        max_length=120,
        blank=True,
        null=True,
    )
    location_city = models.CharField(
        help_text="If not a court or school, the city where person worked.",
        max_length=50,
        blank=True,
    )
    location_state = local_models.USStateField(
        help_text="If not a court or school, the state where person worked.",
        blank=True,
    )
    appointer = models.ForeignKey(
        "self",
        help_text="If this is an appointed position, the person-position "
        "responsible for the appointment. This field references "
        "other positions instead of referencing people because that "
        "allows you to know the position a person held when an "
        "appointment was made.",
        related_name="appointed_positions",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    supervisor = models.ForeignKey(
        Person,
        help_text="If this is a clerkship, the supervising judge.",
        related_name="supervised_positions",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    predecessor = models.ForeignKey(
        Person,
        help_text="The person that previously held this position",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    date_created = models.DateTimeField(
        help_text="The time when this item was created",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    date_nominated = models.DateField(
        help_text="The date recorded in the Senate Executive Journal when a "
        "federal judge was nominated for their position or the date "
        "a state judge nominated by the legislature. When a "
        "nomination is by primary election, this is the date of the "
        "election. When a nomination is from a merit commission, "
        "this is the date the nomination was announced.",
        null=True,
        blank=True,
        db_index=True,
    )
    date_elected = models.DateField(
        help_text="Judges are elected in most states. This is the date of their"
        "first election. This field will be null if the judge was "
        "initially selected by nomination.",
        null=True,
        blank=True,
        db_index=True,
    )
    date_recess_appointment = models.DateField(
        help_text="If a judge was appointed while congress was in recess, this "
        "is the date of that appointment.",
        null=True,
        blank=True,
        db_index=True,
    )
    date_referred_to_judicial_committee = models.DateField(
        help_text="Federal judges are usually referred to the Judicial "
        "Committee before being nominated. This is the date of that "
        "referral.",
        null=True,
        blank=True,
        db_index=True,
    )
    date_judicial_committee_action = models.DateField(
        help_text="The date that the Judicial Committee took action on the "
        "referral.",
        null=True,
        blank=True,
        db_index=True,
    )
    judicial_committee_action = models.CharField(
        help_text="The action that the judicial committee took in response to "
        "a nomination",
        choices=JUDICIAL_COMMITTEE_ACTIONS,
        max_length=20,
        blank=True,
    )
    date_hearing = models.DateField(
        help_text="After being nominated, a judge is usually subject to a "
        "hearing. This is the date of that hearing.",
        null=True,
        blank=True,
        db_index=True,
    )
    date_confirmation = models.DateField(
        help_text="After the hearing the senate will vote on judges. This is "
        "the date of that vote.",
        null=True,
        blank=True,
        db_index=True,
    )
    date_start = models.DateField(
        help_text="The date the position starts active duty.",
        blank=True,
        null=True,
        db_index=True,
    )
    date_granularity_start = models.CharField(
        choices=DATE_GRANULARITIES,
        max_length=15,
        blank=True,
    )
    date_termination = models.DateField(
        help_text="The last date of their employment. The compliment to "
        "date_start",
        null=True,
        blank=True,
        db_index=True,
    )
    termination_reason = models.CharField(
        help_text="The reason for a termination",
        choices=TERMINATION_REASONS,
        max_length=25,
        blank=True,
    )
    date_granularity_termination = models.CharField(
        choices=DATE_GRANULARITIES,
        max_length=15,
        blank=True,
    )
    date_retirement = models.DateField(
        help_text="The date when they become a senior judge by going into "
        "active retirement",
        null=True,
        blank=True,
        db_index=True,
    )
    nomination_process = models.CharField(
        help_text="The process by which a person was nominated into this "
        "position.",
        choices=NOMINATION_PROCESSES,
        max_length=20,
        blank=True,
    )
    vote_type = models.CharField(
        help_text="The type of vote that resulted in this position.",
        choices=VOTE_TYPES,
        max_length=2,
        blank=True,
    )
    voice_vote = models.NullBooleanField(
        help_text="Whether the Senate voted by voice vote for this position.",
        blank=True,
    )
    votes_yes = models.PositiveIntegerField(
        help_text="If votes are an integer, this is the number of votes in "
        "favor of a position.",
        null=True,
        blank=True,
    )
    votes_no = models.PositiveIntegerField(
        help_text="If votes are an integer, this is the number of votes "
        "opposed to a position.",
        null=True,
        blank=True,
    )
    votes_yes_percent = models.FloatField(
        help_text="If votes are a percentage, this is the percentage of votes "
        "in favor of a position.",
        null=True,
        blank=True,
    )
    votes_no_percent = models.FloatField(
        help_text="If votes are a percentage, this is the percentage of votes "
        "opposed to a position.",
        null=True,
        blank=True,
    )
    how_selected = models.CharField(
        help_text="The method that was used for selecting this judge for this "
        "position (generally an election or appointment).",
        choices=SELECTION_METHODS,
        max_length=20,
        blank=True,
    )
    has_inferred_values = models.BooleanField(
        help_text="Some or all of the values for this position were inferred "
        "from a data source instead of manually added. See sources "
        "field for more details.",
        default=False,
    )

    def __str__(self):
        return "%s: %s at %s" % (
            self.pk,
            self.person.name_full,
            self.court_id,
        )

    @property
    def is_judicial_position(self):
        """Return True if the position is judicial."""
        if self.POSITION_TYPE_GROUPS.get(self.position_type) == "Judge":
            return True
        return False

    @property
    def is_clerkship(self):
        """Return True if the position is a clerkship."""
        if self.POSITION_TYPE_GROUPS.get(self.position_type) == "Clerkships":
            return True
        return False

    @property
    def vote_string(self):
        """Make a human-friendly string from the vote information"""
        s = ""

        # Do vote type first
        if self.vote_type == "s":
            s += "Senate voted"
            if self.voice_vote:
                s += ' <span class="alt">by</span> voice vote'
        elif self.vote_type in ["p", "np"]:
            s += self.get_vote_type_display()

        # Then do vote counts/percentages, if we have that info.
        if self.votes_yes or self.votes_yes_percent:
            s += ", "
        if self.votes_yes:
            s += '%s in favor <span class="alt">and</span> %s ' "opposed" % (
                self.votes_yes,
                self.votes_no,
            )
        elif self.votes_yes_percent:
            s += (
                '%g%% in favor <span class="alt">and</span> '
                "%g%% opposed"
                % (self.votes_yes_percent, self.votes_no_percent)
            )
        return s

    @property
    def html_title(self):
        """Display the position as a title."""
        s = ""

        # Title
        if self.get_position_type_display():
            s += self.get_position_type_display()
        else:
            s += self.job_title

        # Where
        if self.school or self.organization_name or self.court:
            s += ' <span class="alt text-lowercase">at</span> '
            if self.court:
                s += self.court.full_name
            elif self.school:
                s += self.school.name
            elif self.organization_name:
                s += self.organization_name

        # When
        if self.date_start or self.date_termination:
            if self.date_termination:
                end_date = granular_date(self, "date_termination")
            else:
                # If we don't know when the position ended, we use a ? if the
                # person has died, or say Present if they're alive.
                if self.person.date_dod:
                    end_date = "?"
                else:
                    end_date = "Present"

            s += ' <span class="text-capitalize">(%s &ndash; %s)' % (
                granular_date(self, "date_start", default="Unknown Date"),
                end_date,
            )
        return s

    @property
    def sorted_appointed_positions(self):
        """Appointed positions, except sorted by date instead of name."""
        return self.appointed_positions.all().order_by("-date_start")

    def save(self, *args, **kwargs):
        self.full_clean()
        super(Position, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        validate_partial_date(self, ["start", "termination"])
        validate_is_not_alias(
            self, ["person", "supervisor", "predecessor", "school"]
        )
        validate_at_most_n(self, 1, ["school", "organization_name", "court"])
        validate_exactly_n(self, 1, ["position_type", "job_title"])
        validate_all_or_none(self, ["votes_yes", "votes_no"])
        validate_all_or_none(self, ["votes_yes_percent", "votes_no_percent"])
        validate_not_all(
            self,
            ["votes_yes", "votes_no", "votes_yes_percent", "votes_no_percent"],
        )
        validate_nomination_fields_ok(self)
        validate_supervisor(self)

        super(Position, self).clean_fields(*args, **kwargs)


class RetentionEvent(models.Model):
    RETENTION_TYPES = (
        ("reapp_gov", "Governor Reappointment"),
        ("reapp_leg", "Legislative Reappointment"),
        ("elec_p", "Partisan Election"),
        ("elec_n", "Nonpartisan Election"),
        ("elec_u", "Uncontested Election"),
    )
    position = models.ForeignKey(
        Position,
        help_text="The position that was retained by this event.",
        related_name="retention_events",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    retention_type = models.CharField(
        help_text="The method through which this position was retained.",
        choices=RETENTION_TYPES,
        max_length=10,
    )
    date_retention = models.DateField(
        help_text="The date of retention",
        db_index=True,
    )
    votes_yes = models.PositiveIntegerField(
        help_text="If votes are an integer, this is the number of votes in "
        "favor of a position.",
        null=True,
        blank=True,
    )
    votes_no = models.PositiveIntegerField(
        help_text="If votes are an integer, this is the number of votes "
        "opposed to a position.",
        null=True,
        blank=True,
    )
    votes_yes_percent = models.FloatField(
        help_text="If votes are a percentage, this is the percentage of votes "
        "in favor of a position.",
        null=True,
        blank=True,
    )
    votes_no_percent = models.FloatField(
        help_text="If votes are a percentage, this is the percentage of votes "
        "opposed to a position.",
        null=True,
        blank=True,
    )
    unopposed = models.NullBooleanField(
        help_text="Whether the position was unopposed at the time of "
        "retention.",
        null=True,
        blank=True,
    )
    won = models.NullBooleanField(
        help_text="Whether the retention event was won.",
        null=True,
        blank=True,
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super(RetentionEvent, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        validate_all_or_none(self, ["votes_yes", "votes_no"])
        validate_all_or_none(self, ["votes_yes_percent", "votes_no_percent"])
        super(RetentionEvent, self).clean_fields(*args, **kwargs)


class Education(models.Model):
    DEGREE_LEVELS = (
        ("ba", "Bachelor's (e.g. B.A.)"),
        ("ma", "Master's (e.g. M.A.)"),
        ("jd", "Juris Doctor (J.D.)"),
        ("llm", "Master of Laws (LL.M)"),
        ("llb", "Bachelor of Laws (e.g. LL.B)"),
        ("jsd", "Doctor of Law (J.S.D)"),
        ("phd", "Doctor of Philosophy (PhD)"),
        ("aa", "Associate (e.g. A.A.)"),
        ("md", "Medical Degree (M.D.)"),
        ("mba", "Master of Business Administration (M.B.A.)"),
        ("cfa", "Accounting Certification (C.P.A., C.M.A., C.F.A.)"),
        ("cert", "Certificate"),
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    person = models.ForeignKey(
        Person,
        help_text="The person that completed this education",
        related_name="educations",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    school = models.ForeignKey(
        School,
        help_text="The school where this education was compeleted",
        related_name="educations",
        on_delete=models.CASCADE,
    )
    degree_level = models.CharField(
        help_text="Normalized degree level, e.g. BA, JD.",
        choices=DEGREE_LEVELS,
        max_length=4,
        blank=True,
    )
    degree_detail = models.CharField(
        help_text="Detailed degree description, e.g. including major.",
        max_length=100,
        blank=True,
    )
    degree_year = models.PositiveSmallIntegerField(
        help_text="The year the degree was awarded.",
        null=True,
        blank=True,
    )

    def __str__(self):
        return "%s: Degree in %s from %s in the year %s" % (
            self.pk,
            self.degree_detail,
            self.school.name,
            self.degree_year,
        )

    def save(self, *args, **kwargs):
        self.full_clean()
        super(Education, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        # Note that this isn't run during updates, alas.
        validate_is_not_alias(self, ["person", "school"])
        super(Education, self).clean_fields(*args, **kwargs)


class Race(models.Model):
    RACES = (
        ("w", "White"),
        ("b", "Black or African American"),
        ("i", "American Indian or Alaska Native"),
        ("a", "Asian"),
        ("p", "Native Hawaiian or Other Pacific Islander"),
        ("h", "Hispanic/Latino"),
    )
    race = models.CharField(
        choices=RACES,
        max_length=5,
        unique=True,
    )

    def __str__(self):
        # This is used in the API via the StringRelatedField. Do not cthange.
        return "{race}".format(race=self.race)


class PoliticalAffiliation(models.Model):
    POLITICAL_AFFILIATION_SOURCE = (
        ("b", "Ballot"),
        ("a", "Appointer"),
        ("o", "Other"),
    )
    POLITICAL_PARTIES = (
        ("d", "Democratic"),
        ("r", "Republican"),
        ("i", "Independent"),
        ("g", "Green"),
        ("l", "Libertarian"),
        ("f", "Federalist"),
        ("w", "Whig"),
        ("j", "Jeffersonian Republican"),
        ("u", "National Union"),
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    person = models.ForeignKey(
        Person,
        help_text="The person with the political affiliation",
        related_name="political_affiliations",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    political_party = models.CharField(
        help_text="The political party the person is affiliated with.",
        choices=POLITICAL_PARTIES,
        max_length=5,
    )
    source = models.CharField(
        help_text="The source of the political affiliation -- where it is "
        "documented that this affiliation exists.",
        choices=POLITICAL_AFFILIATION_SOURCE,
        max_length=5,
        blank=True,
    )
    date_start = models.DateField(
        help_text="The date the political affiliation was first documented",
        null=True,
        blank=True,
    )
    date_granularity_start = models.CharField(
        choices=DATE_GRANULARITIES,
        max_length=15,
        blank=True,
    )
    date_end = models.DateField(
        help_text="The date the affiliation ended.",
        null=True,
        blank=True,
    )
    date_granularity_end = models.CharField(
        choices=DATE_GRANULARITIES,
        max_length=15,
        blank=True,
    )

    def save(self, *args, **kwargs):
        self.full_clean()
        super(PoliticalAffiliation, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        validate_partial_date(self, ["start", "end"])
        validate_is_not_alias(self, ["person"])
        super(PoliticalAffiliation, self).clean_fields(*args, **kwargs)


class Source(models.Model):
    person = models.ForeignKey(
        Person,
        related_name="sources",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified",
        auto_now=True,
        db_index=True,
    )
    url = models.URLField(
        help_text="The URL where this data was gathered.",
        max_length=2000,
        blank=True,
    )
    date_accessed = models.DateField(
        help_text="The date the data was gathered.",
        blank=True,
        null=True,
    )
    notes = models.TextField(
        help_text="Any additional notes about the data's provenance, in "
        "Markdown format.",
        blank=True,
    )


class ABARating(models.Model):
    ABA_RATINGS = (
        ("ewq", "Exceptionally Well Qualified"),
        ("wq", "Well Qualified"),
        ("q", "Qualified"),
        ("nq", "Not Qualified"),
        ("nqa", "Not Qualified By Reason of Age"),
    )
    person = models.ForeignKey(
        Person,
        help_text="The person rated by the American Bar Association",
        related_name="aba_ratings",
        on_delete=models.CASCADE,
        blank=True,
        null=True,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    year_rated = models.PositiveSmallIntegerField(
        help_text="The year of the rating.", null=True
    )
    rating = models.CharField(
        help_text="The rating given to the person.",
        choices=ABA_RATINGS,
        max_length=5,
    )

    class Meta:
        verbose_name = "American Bar Association Rating"
        verbose_name_plural = "American Bar Association Ratings"

    def save(self, *args, **kwargs):
        self.full_clean()
        super(ABARating, self).save(*args, **kwargs)

    def clean_fields(self, *args, **kwargs):
        validate_is_not_alias(self, ["person"])
        super(ABARating, self).clean_fields(*args, **kwargs)


class FinancialDisclosure(models.Model):
    """A simple table to hold references to financial disclosure forms"""

    NOMINATION = 0
    INITIAL = 1
    ANNUAL = 2
    FINAL = 3

    REPORT_TYPES = (
        (NOMINATION, "Nomination"),
        (INITIAL, "Initial"),
        (ANNUAL, "Annual"),
        (FINAL, "Final"),
    )

    # FORM CODES
    A = "A"  # 1 - 1000
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H1 = "H1"
    H2 = "H2"
    J = "J"
    K = "K"
    L = "L"
    M = "M"
    N = "N"
    O = "O"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"  # $50,000,001 and up

    # Value method calculations
    Q = "Appraisal"
    R = "Cost (Real Estate Only)"
    S = "Assessment"
    T = "Cash Market"
    U = "Book Value"
    V = "Other"

    # Failed Extraction
    X = "Failed Extraction"

    VALUE_METHOD_CODES = (
        (Q, "Appraisal"),
        (R, "Cost (Real Estate Only)"),
        (S, "Assessment"),
        (T, "Cash Market"),
        (U, "Book Value"),
        (V, "Other"),
        (X, "Failed Extraction"),
    )

    # INCOME GAIN CODES
    INCOME_GAIN_CODES = (
        (A, "1 - 1,000"),
        (B, "1,001 - 2,500"),
        (C, "2,501 - 5,000"),
        (D, "5,001 - 15,000"),
        (E, "15,001 - 50,000"),
        (F, "50,001 - 100,000"),
        (G, "100,001 - 1,000,000"),
        (H1, "1,000,001 - 5,000,000"),
        (H2, "5,000,001 +"),
        (X, "Failed Extraction"),
    )

    GROSS_VALUE_CODES = (
        (J, "1 - 15,000"),
        (K, "15,001 - 50,000"),
        (L, "50,001 - 100,000"),
        (M, "100,001 - 250,000"),
        (N, "250,001 - 500,000"),
        (O, "500,001 - 1,000,000"),
        (P1, "1,000,001 - 5,000,000"),
        (P2, "5,000,001 - 25,000,000"),
        (P3, "25,000,001 - 50,000,000"),
        (P4, "50,000,001 - "),
        (X, "Failed Extraction"),
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    person = models.ForeignKey(
        Person,
        help_text="The person that the document is associated with.",
        related_name="financial_disclosures",
        on_delete=models.CASCADE,
    )
    year = models.SmallIntegerField(
        help_text="The year that the disclosure corresponds with",
        db_index=True,
    )
    filepath = models.FileField(
        help_text="The disclosure report itself",
        upload_to=make_pdf_path,
        storage=AWSMediaStorage(),
        db_index=True,
    )
    thumbnail = models.FileField(
        help_text="A thumbnail of the first page of the disclosure form",
        upload_to="financial-disclosures/thumbnails/",
        storage=IncrementingFileSystemStorage(),
        # upload_to=make_pdf_path,
        # storage=AWSMediaStorage(),
        null=True,
        blank=True,
    )
    thumbnail_status = models.SmallIntegerField(
        help_text="The status of the thumbnail generation",
        choices=THUMBNAIL_STATUSES.NAMES,
        default=THUMBNAIL_STATUSES.NEEDED,
    )
    page_count = models.SmallIntegerField(
        help_text="The number of pages in the disclosure report",
    )
    pdf_hash = models.CharField(
        help_text="PDF hash, used to idenitify duplicate PDFs",
        max_length=40,
        db_index=True,
        blank=True,
    )
    report_type = models.TextField(
        help_text="Financial Disclosure report type",
        choices=REPORT_TYPES,
        blank=True,
        null=True,
    )
    is_amended = models.BooleanField(
        help_text="Is disclsoure amended?",
        default=False,
    )
    addendum_content_raw = models.TextField(
        help_text="Raw content of addendum.",
        blank=True,
    )
    addendum_redacted = models.BooleanField(
        help_text="Is the adendum partially or completely redacted?",
        default=False,
    )
    has_been_extracted = models.BooleanField(
        help_text="Have we successfully extracted the data from PDF?",
        default=False,
    )

    class Meta:
        ordering = ("-year",)

    def calculate(self, field_name):
        """Calculate gross value of all investments in disclosure

        We can calculate the total investment for four fields

        ** gross_value_code - Gross Value total for the invesments
        ** income_during_reporting_period_code - Gross Income
        ** transaction_gain_code  - Total Income gain
        ** transaction_value_code - Total Transaction values

        :param disclosure: Financial Disclosure
        :return: Wealth calculated
        """
        wealth = {}
        min_max = (0, 0)
        investments = (
            Investment.objects.filter(financial_disclosure=self)
            .exclude(**{field_name: "•"})
            .exclude(**{field_name: ""})
        )
        wealth["miss_count"] = (
            Investment.objects.filter(financial_disclosure=self)
            .filter(**{field_name: "•"})
            .count()
        )
        for investment in investments:
            id = getattr(investment, f"get_{field_name}_display")()
            if id is None:
                wealth["msg"] = "Field not recognized"
                return wealth
            new_min_max = re.findall(r"([0-9,]{1,12})", id)
            new_min_max = [int(x.replace(",", "")) for x in new_min_max]
            min_max = [i + j for i, j in zip(min_max, new_min_max)]
        wealth["min"], wealth["max"] = min_max
        return wealth

    def save(self, *args, **kwargs):
        super(FinancialDisclosure, self).save(*args, **kwargs)
        if not self.pk:
            from cl.people_db.tasks import (
                make_financial_disclosure_thumbnail_from_pdf,
            )

            make_financial_disclosure_thumbnail_from_pdf.delay(self.pk)


class Investment(models.Model):
    """ Financial Disclosure Investments Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="investment",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    description = models.TextField(help_text="Name of investment", blank=True)

    redacted = models.BooleanField(
        help_text="Whether investment contains redactions.",
        default=False,
    )
    income_during_reporting_period_code = models.TextField(
        help_text="Increase in investment value - as a form code",
        choices=FinancialDisclosure.INCOME_GAIN_CODES,
        blank=True,
    )
    income_during_reporting_period_type = models.TextField(
        help_text="Type of investment ex. Rent, Dividend.  Typically "
        "standardized but not universally",
        blank=True,
    )
    gross_value_code = models.TextField(
        help_text="Investment total value code at end of reporting period as code",
        choices=FinancialDisclosure.GROSS_VALUE_CODES,
        blank=True,
    )
    gross_value_method = models.TextField(
        help_text="Investment valuation method code",
        choices=FinancialDisclosure.VALUE_METHOD_CODES,
        blank=True,
    )

    # Transactions indicate if the investment was bought sold etc.
    # during the reporting period
    transaction_during_reporting_period = models.TextField(
        help_text="Transaction of investment during reporting period ex. Buy Sold etc.",
        blank=True,
    )
    transaction_date_raw = models.TextField(
        help_text="Date of the transaction, if any", blank=True
    )
    transaction_value_code = models.TextField(
        help_text="Transaction value amount, as form code",
        choices=FinancialDisclosure.GROSS_VALUE_CODES,
        blank=True,
    )
    transaction_gain_code = models.TextField(
        help_text="Gain from investment transaction if any",
        choices=FinancialDisclosure.INCOME_GAIN_CODES,
        blank=True,
    )
    transaction_partner = models.TextField(
        help_text="Identity of the transaction partner", blank=True
    )

    has_inferred_values = models.BooleanField(
        help_text="The investment name was inferred during extraction",
        default=False,
    )


class Positions(models.Model):
    """ Financial Disclosure Position Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="positions",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    position = models.TextField(
        help_text="Position title.",
        blank=True,
    )
    organization_name = models.TextField(
        help_text="Name of orgnization or entity.",
        blank=True,
    )
    redacted = models.BooleanField(
        help_text="Financial Disclosure filing option",
        default=False,
    )


class Agreements(models.Model):
    """ Financial Disclosure Agreements Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="agreements",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    date = models.TextField(
        help_text="Dates of judicial agreements.",
        blank=True,
    )
    parties_and_terms = models.TextField(
        help_text="Parties and terms of agreement.",
        blank=True,
    )
    redacted = models.BooleanField(
        help_text="Is the agreement redacted?",
        default=False,
    )


class NonInvestmentIncome(models.Model):
    """Financial Disclosure Non Investment Income Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="non_investment_income",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    date = models.TextField(
        help_text="Date of non-investment income (ex. 2011).",
        blank=True,
    )
    source_type = models.TextField(
        help_text="Source and type of non-investment income for the judge "
        "(ex. Teaching a class at U. Miami).",
        blank=True,
    )
    income_amount = models.TextField(
        help_text="Amount earned by judge.",
        blank=True,
    )
    redacted = models.BooleanField(
        help_text="Is non-investment income redacted?",
        default=False,
    )


class SpouseIncome(models.Model):
    """ Financial Disclosure Judge Spouse Income Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="spouse_income",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    source_type = models.TextField(
        help_text="Source and Type of income of judicial spouse",
        blank=True,
    )
    date = models.TextField(
        help_text="Date of spousal income (ex. 2011).",
        blank=True,
    )
    redacted = models.TextField(
        help_text="Is judicial spousal income redacted?",
        default=False,
    )


class Reimbursement(models.Model):
    """Reimbursements listed in judicial disclosure"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="reimbursement",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    source = models.TextField(
        help_text="Source of the reimbursement (ex. FSU Law School).",
        blank=True,
    )
    dates = models.TextField(
        help_text="Dates as a text string for the date of reimbursements."
        "This is often conference dates (ex. June 2-6, 2011).",
        blank=True,
    )
    location = models.TextField(
        help_text="Location of the reimbursement "
        "(ex. Harvard Law School, Cambridge, MA).",
        blank=True,
    )
    purpose = models.TextField(
        help_text="Purpose of the reimbursement (ex. Baseball announcer).",
        blank=True,
    )
    items_paid_or_provided = models.TextField(
        help_text="Items reimbursed (ex. Room, Airfare).",
        blank=True,
    )
    redacted = models.BooleanField(
        help_text="Does the reimbursement contain redactions?",
        default=False,
    )


class Gift(models.Model):
    """ Financial Disclosure Gifts Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="gift",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )

    source = models.TextField(
        help_text="Source of the judicial gift.",
        blank=True,
    )
    description = models.TextField(
        help_text="Description of the gift.",
        blank=True,
    )
    value_code = models.TextField(
        help_text="Value of the judicial gift, as Value Code.",
        choices=FinancialDisclosure.GROSS_VALUE_CODES,
        blank=True,
    )
    redacted = models.BooleanField(
        help_text="Is the gift redacted?",
        default=False,
    )


class Debt(models.Model):
    """ Financial Disclosure Judicial Debts/Liabilities Table"""

    financial_disclosure = models.ForeignKey(
        FinancialDisclosure,
        help_text="The financial disclosure associated with this investment.",
        related_name="debt",
        on_delete=models.CASCADE,
    )
    date_created = models.DateTimeField(
        help_text="The original creation date for the item",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    creditor_name = models.TextField(
        help_text="Liability/Debt creditor", blank=True
    )
    description = models.TextField(
        help_text="Description of the debt", blank=True
    )
    value_code = models.TextField(
        help_text="Form code for the value of the judicial debt.",
        choices=FinancialDisclosure.GROSS_VALUE_CODES,
        blank=True,
    )
    redacted = models.BooleanField(
        help_text="Is the debt redacted?",
        default=False,
    )


class PartyType(models.Model):
    """Links together the parties and the docket. Probably a poorly named
    model.

    (It made sense at the time.)
    """

    docket = models.ForeignKey(
        "search.Docket",
        related_name="party_types",
        on_delete=models.CASCADE,
    )
    party = models.ForeignKey(
        "Party",
        related_name="party_types",
        on_delete=models.CASCADE,
    )
    name = models.CharField(
        help_text="The name of the type (Defendant, Plaintiff, etc.)",
        max_length=100,  # 2× the max in first 100,000 sampled.
        db_index=True,
    )
    date_terminated = models.DateField(
        help_text="The date that the party was terminated from the case, if "
        "applicable.",
        null=True,
        blank=True,
    )
    extra_info = models.TextField(
        help_text="Additional info from PACER",
        db_index=True,
        blank=True,
    )
    highest_offense_level_opening = models.TextField(
        help_text="In a criminal case, the highest offense level at the "
        "opening of the case.",
        blank=True,
    )
    highest_offense_level_terminated = models.TextField(
        help_text="In a criminal case, the highest offense level at the end "
        "of the case.",
        blank=True,
    )

    class Meta:
        unique_together = ("docket", "party", "name")

    def __str__(self):
        return "%s: Party %s is %s in Docket %s" % (
            self.pk,
            self.party_id,
            self.name,
            self.docket_id,
        )


class CriminalCount(models.Model):
    """The criminal counts associated with a PartyType object (i.e., associated
    with a party in a docket.
    """

    PENDING = 1
    TERMINATED = 2
    COUNT_STATUSES = (
        (PENDING, "Pending"),
        (TERMINATED, "Terminated"),
    )
    party_type = models.ForeignKey(
        PartyType,
        help_text="The docket and party the counts are associated with.",
        related_name="criminal_counts",
        on_delete=models.CASCADE,
    )
    name = models.TextField(
        help_text="The name of the count, such as '21:952 and 960 - "
        "Importation of Marijuana(1)'.",
    )
    disposition = models.TextField(
        help_text="The disposition of the count, such as 'Custody of BOP for "
        "60 months, followed by 4 years supervised release. No "
        "fine. $100 penalty assessment.",
        # Can be blank if no disposition yet.
        blank=True,
    )
    status = models.SmallIntegerField(
        help_text="Whether the count is pending or terminated.",
        choices=COUNT_STATUSES,
    )

    @staticmethod
    def normalize_status(status_str):
        """Convert a status string into one of COUNT_STATUSES"""
        if status_str == "pending":
            return CriminalCount.PENDING
        elif status_str == "terminated":
            return CriminalCount.TERMINATED


class CriminalComplaint(models.Model):
    """The criminal complaints associated with a PartyType object (i.e.,
    associated with a party in a docket.
    """

    party_type = models.ForeignKey(
        PartyType,
        help_text="The docket and party the complaints are associated with.",
        related_name="criminal_complaints",
        on_delete=models.CASCADE,
    )
    name = models.TextField(
        help_text="The name of the criminal complaint, for example, '8:1326 "
        "Reentry of Deported Alien'",
    )
    disposition = models.TextField(
        help_text="The disposition of the criminal complaint.",
        blank=True,
    )


class Party(models.Model):
    date_created = models.DateTimeField(
        help_text="The time when this item was created",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    attorneys = models.ManyToManyField(
        "Attorney",
        help_text="The attorneys involved with the party.",
        through="Role",
        related_name="parties",
    )
    name = models.TextField(
        help_text="The name of the party.",
        db_index=True,
    )
    extra_info = models.TextField(
        # See: 7d4c916a34207c3c55b58cc385425a9fc7021004
        help_text="Prior to March, 2018, this field briefly held additional "
        "info from PACER about particular parties. That was a modelling "
        "mistake and the information has been moved to the "
        "PartyType.extra_info field instead. This field will be removed in "
        "October, 2020.",
        db_index=True,
    )

    class Meta:
        verbose_name_plural = "Parties"
        permissions = (("has_recap_api_access", "Can work with RECAP API"),)

    def __str__(self):
        return "%s: %s" % (self.pk, self.name)


class Role(models.Model):
    """Links together the party, the attorney, and the docket"""

    ATTORNEY_TO_BE_NOTICED = 1
    ATTORNEY_LEAD = 2
    ATTORNEY_IN_SEALED_GROUP = 3
    PRO_HAC_VICE = 4
    SELF_TERMINATED = 5
    TERMINATED = 6
    SUSPENDED = 7
    INACTIVE = 8
    DISBARRED = 9
    UNKNOWN = 10
    ATTORNEY_ROLES = (
        (ATTORNEY_TO_BE_NOTICED, "Attorney to be noticed"),
        (ATTORNEY_LEAD, "Lead attorney"),
        (ATTORNEY_IN_SEALED_GROUP, "Attorney in sealed group"),
        (PRO_HAC_VICE, "Pro hac vice"),
        (SELF_TERMINATED, "Self-terminated"),
        (TERMINATED, "Terminated"),
        (SUSPENDED, "Suspended"),
        (INACTIVE, "Inactive"),
        (DISBARRED, "Disbarred"),
        (UNKNOWN, "Unknown"),
    )
    party = models.ForeignKey(
        Party,
        related_name="roles",
        on_delete=models.CASCADE,
    )
    attorney = models.ForeignKey(
        "Attorney",
        related_name="roles",
        on_delete=models.CASCADE,
    )
    docket = models.ForeignKey(
        "search.Docket",
        help_text="The attorney represented the party on this docket in this "
        "role.",
        on_delete=models.CASCADE,
    )
    role = models.SmallIntegerField(
        help_text="The name of the attorney's role. Used primarily in "
        "district court cases.",
        choices=ATTORNEY_ROLES,
        db_index=True,
        null=True,
    )
    role_raw = models.TextField(
        help_text="The raw value of the role, as a string. Items prior to "
        "2018-06-06 may not have this value.",
        blank=True,
    )
    date_action = models.DateField(
        help_text="The date the attorney was disbarred, suspended, "
        "terminated...",
        null=True,
    )

    class Meta:
        unique_together = (
            "party",
            "attorney",
            "role",
            "docket",
            "date_action",
        )

    def __str__(self):
        return "%s: Attorney %s is %s for Party %s in docket %s" % (
            self.pk,
            self.attorney_id,
            self.get_role_display(),
            self.party_id,
            self.docket_id,
        )


class Attorney(models.Model):
    date_created = models.DateTimeField(
        help_text="The time when this item was created",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    organizations = models.ManyToManyField(
        "AttorneyOrganization",
        help_text="The organizations that the attorney is affiliated with",
        related_name="attorneys",
        through="AttorneyOrganizationAssociation",
    )
    name = models.TextField(
        help_text="The name of the attorney.",
        db_index=True,
    )
    contact_raw = models.TextField(
        help_text="The raw contents of the contact field",
        db_index=True,
    )
    phone = local_models.PhoneNumberField(
        help_text="The phone number of the attorney.",
    )
    fax = local_models.PhoneNumberField(
        help_text="The fax number of the attorney.",
    )
    email = models.EmailField(
        help_text="The email address of the attorney.",
    )

    class Meta:
        permissions = (("has_recap_api_access", "Can work with RECAP API"),)

    def __str__(self):
        return "%s: %s" % (self.pk, self.name)


class AttorneyOrganizationAssociation(models.Model):
    """A through table linking an attorneys with organizations.

    E.g: "This attorney worked at this organization on this case."

    This way, we when we know that an attorney worked at four different firms,
    we also know what they did while there.
    """

    attorney = models.ForeignKey(
        Attorney,
        related_name="attorney_organization_associations",
        on_delete=models.CASCADE,
    )
    attorney_organization = models.ForeignKey(
        "AttorneyOrganization",
        related_name="attorney_organization_associations",
        on_delete=models.CASCADE,
    )
    docket = models.ForeignKey(
        "search.Docket",
        help_text="The docket that the attorney worked on while at this "
        "organization.",
        on_delete=models.CASCADE,
    )

    class Meta:
        unique_together = ("attorney", "attorney_organization", "docket")

    def __str__(self):
        return "%s: Atty %s worked on docket %s while at org %s" % (
            self.pk,
            self.attorney_id,
            self.docket_id,
            self.attorney_organization_id,
        )


class AttorneyOrganization(models.Model):
    date_created = models.DateTimeField(
        help_text="The time when this item was created",
        auto_now_add=True,
        db_index=True,
    )
    date_modified = models.DateTimeField(
        help_text="The last moment when the item was modified.",
        auto_now=True,
        db_index=True,
    )
    lookup_key = models.TextField(
        help_text="A trimmed version of the address for duplicate matching.",
        db_index=True,
        unique=True,
    )
    name = models.TextField(
        help_text="The name of the organization.",
        db_index=True,
    )
    address1 = models.TextField(
        help_text="The normalized address1 of the organization",
        db_index=True,
    )
    address2 = models.TextField(
        help_text="The normalized address2 of the organization",
        db_index=True,
    )
    city = models.TextField(
        help_text="The normalized city of the organization",
        db_index=True,
    )
    state = local_models.USPostalCodeField(
        help_text="The two-letter USPS postal abbreviation for the "
        "organization",
        db_index=True,
    )
    zip_code = local_models.USZipCodeField(
        help_text="The zip code for the organization, XXXXX or XXXXX-XXXX "
        "work.",
        db_index=True,
    )

    class Meta:
        unique_together = (
            "name",
            "address1",
            "address2",
            "city",
            "state",
            "zip_code",
        )

    def __str__(self):
        return "%s: %s, %s, %s, %s, %s, %s" % (
            self.pk,
            self.name,
            self.address1,
            self.address2,
            self.city,
            self.state,
            self.zip_code,
        )
