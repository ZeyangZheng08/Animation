using System.Collections;
using System.Collections.Generic;
using UnityEngine;

public class NurseAnimatorEvents : MonoBehaviour
{
	[SerializeField] private Animator nurseAnimator;

	//Needed components for the ambubag visibility and compression animation
    [Header("BVM Ambubag Animation")]
	[Tooltip("Assign the held_ambubag attached to the nurse hand here")]
	[SerializeField] private GameObject ambubag;
	[Tooltip("Assign the child object with the SkinnedMeshRenderer blendshape for bag compression")]
	[SerializeField] private SkinnedMeshRenderer bagMesh;
    [Tooltip("Controls how quickly the bag compresses/inflates")]

    //TODO: make compatible with animation speed controller
	public float compressSpeed = 100;
    //Components extracted from the provided variables
	private SkinnedMeshRenderer ambubagMesh;
	private bool compressStart = false;
	private bool inflateBag = true;

	// Whether bagMesh actually has the blendshape this script drives. Resolved once at Start.
	//
	// It was assigned to `abvrm_face_mask`, which has NO blendshapes, while the squeeze lives on
	// `abvrm_self_inflatingbag` ("abvrm_blendShape.squish"). So every frame on every nurse threw
	// "Array index (0) is out of bounds (size=0)" out of Update, the bag never compressed, and the
	// console filled up fast enough to hide anything else. Reference fixed in the scene; this flag is
	// so a wrong one degrades to doing nothing instead of throwing sixty times a second.
	private bool bagCanSquish = false;

    [Header("Medicine Bottle Animation")]
	[Tooltip("Assign the held_aspirin_bottle attached to the nurse hand here")]
    public GameObject medicineBottle;

	// Start is called before the first frame update
	void Start()
    {
		// Error checking for BVM Ambubag Animation
		if (ambubag != null)
		{
			if (bagMesh == null)
			{
				Debug.LogWarning("NurseAnimatorEvents: bagMesh has no SkinnedMeshRenderer");
			}
		} else
		{
			Debug.LogWarning("NurseAnimatorEvents: No ambubag assigned");
		}

		// Checked here rather than per frame, and it names the mesh that WOULD work. A renderer with no
		// blendshapes is a plausible thing to drag into this slot -- the ambubag has ten of them and
		// only one carries the squeeze -- so the wrong one has to say so once instead of throwing on
		// every Update.
		bagCanSquish = bagMesh != null && bagMesh.sharedMesh != null
					   && bagMesh.sharedMesh.blendShapeCount > 0;
		if (bagMesh != null && !bagCanSquish)
		{
			Debug.LogWarning("NurseAnimatorEvents on " + name + ": bagMesh is '" + bagMesh.name
							 + "', which has no blendshapes, so the bag cannot compress. Assign the "
							 + "renderer that carries the squeeze blendshape "
							 + "(abvrm_self_inflatingbag on this ambubag).");
		}

		if (medicineBottle == null)
			Debug.LogWarning("NurseAnimatorEvents: No medicineBottle assigned");
	}

    // Update is called once per frame
    void Update()
    {
		//update ambubag blendshape values
		ambubagAnimation();
		//check for animator bool Hold Pills to be false
		holdPillAnimation();
	}

	// Handles inflation/compression of ambubag
	void ambubagAnimation()
	{
		if (!bagCanSquish)
			return;

		float currentBlendValue = bagMesh.GetBlendShapeWeight(0);
		if (compressStart)
		{
			// CLAMPED, because the step is deltaTime-sized and the bounds are checked before it rather
			// than after: the last frame of a cycle overshoots by however much of the step was left.
			// Measured on one cycle here, the bag settled at -1.12 instead of 0 -- a blendshape weight
			// below zero extrapolates past the neutral shape, so the bag ends slightly inside-out.
			if (inflateBag)
			{
				if (currentBlendValue < 100f)
				{
					bagMesh.SetBlendShapeWeight(0,
						Mathf.Min(100f, currentBlendValue + Time.deltaTime * compressSpeed));
				} else if (currentBlendValue >= 100f)
				{
					inflateBag = false;
				}
			} else
			{
				if (currentBlendValue > 0f)
				{
					bagMesh.SetBlendShapeWeight(0,
						Mathf.Max(0f, currentBlendValue - Time.deltaTime * compressSpeed));
				} else if (currentBlendValue <= 0f)
				{
					compressStart = false;
					inflateBag = true;
				}
			}
		}
	}

	// Listens for the HoldPills bool to become false outside of animation events
	void holdPillAnimation()
	{
		if(nurseAnimator != null && medicineBottle != null && nurseAnimator.GetBool("Hold Pills") == false)
			medicineBottle.SetActive(false);
	}

	// ANIMATION EVENTS //////////////////////////////////////////////////////////

	//Toggles the visibility of the ambubag
	public void ToggleAmbubagVisibility(string visibility)
	{
		if (visibility == "true")
			ambubag.SetActive(true);
		else if (visibility == "false")
			ambubag.SetActive(false);
		else
			Debug.LogWarning("NurseAnimatorEvents: Unexpected value in ToggleAmbubagVisibility. Please use \"true\" or \"false\"");
	}
	
	// Called by animation clip 'nurse_bvm_2'
	public void AmbubagCompress()
	{
		compressStart = true;
	}

	// Called by animation clip 'nurse_grab_bottle'
	// Toggles the visibility of held medicine bottle (after it is picked up)
	public void HoldMedicineBottle()
	{
		medicineBottle.SetActive(true);
		nurseAnimator.SetBool("Hold Pills", true);
	}

	public void ReleaseMedicineBottle()
	{
		medicineBottle.SetActive(false);
		nurseAnimator.SetBool("Hold Pills", false);
	}

	public void PrepareForBvmCycle()
	{
		compressStart = false;
		inflateBag = true;

		if (bagCanSquish)
			bagMesh.SetBlendShapeWeight(0, 0f);

		if (ambubag != null)
			ambubag.SetActive(true);
	}

	public void ResetRuntimeProps(bool hideAmbubag = true)
	{
		compressStart = false;
		inflateBag = true;

		if (bagCanSquish)
			bagMesh.SetBlendShapeWeight(0, 0f);

		if (hideAmbubag && ambubag != null)
			ambubag.SetActive(false);

		if (medicineBottle != null)
			medicineBottle.SetActive(false);

		if (nurseAnimator != null)
			nurseAnimator.SetBool("Hold Pills", false);
	}

}
